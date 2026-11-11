# Contrato de integración — Motor de Contenido ↔ Portal Storias

Este documento es la referencia compartida entre las dos mitades del proyecto para que Claude (motor de contenido) y Codex (portal SaaS) puedan trabajar en paralelo sin pisarse ni divergir en supuestos. Cualquier cambio a una firma de función, tabla o campo de acá se actualiza en este documento ANTES de tocar el código de los dos lados.

**Cómo usarlo:** guardar este archivo como `AGENTS.md` en la raíz del repo (Codex CLI lo lee automático como instrucciones de repo) y una copia o symlink como `CLAUDE.md` (Claude Code hace lo mismo). Así ninguna de las dos IAs necesita que se lo pegues a mano en cada sesión.

---

## 1. Principio de división

Un solo repo, dos zonas con dueño y responsabilidad separados:

- **`app/engine/`** — dueño: Gonza + Claude. Funciones puras de generación y edición de contenido (prompt a Claude Vision, composición de imagen con Pillow, subida a Cloudinary, publicación en Meta). **Nunca toca Supabase ni Drive directamente para leer/escribir estado** — recibe todo lo que necesita como parámetro y devuelve datos. Es el mismo motor de `C:\Cuan\main.py`, generalizado para recibir el prompt/config por cliente en vez de tenerlo hardcodeado.
- **Todo lo demás** (`app/routers/`, `app/services/`, `app/models/`, `app/db/`, `templates/`, tareas de Celery) — dueño: Matías + Codex. Dueño de la base de datos, la cola de trabajo, la auth, el portal de empleados y de conectar Drive para armar el pool de imágenes disponibles. Es quien **llama** a las funciones de `app/engine` y persiste lo que devuelven.

Hoy una sola persona mantiene las dos zonas; la división se mantiene igual porque el motor puro es más fácil de probar.

Regla dura: ningún archivo se edita desde los dos lados a la vez. Si hace falta un cambio de contrato (agregar un campo, cambiar una firma), se actualiza este documento primero y se avisa al otro lado.

---

## 2. Tablas de Supabase que el engine necesita que existan (las crea/migra el lado Codex)

Extienden el `supabase_schema.sql` que ya armó Matías.

**`clients`** — agregar columnas:
| Campo | Tipo | Notas |
|---|---|---|
| `business_description` | text | Personaje/tono estable del cliente — reemplaza el bloque hardcodeado "Sos el copywriter de CUAN..." de `main.py` |
| `tone_examples` | jsonb | Array de hilos de ejemplo, cada uno como array de 4 strings — reemplaza los 6 ejemplos hardcodeados |
| `topics` | jsonb | Array de temas posibles (strings). Si es null/vacío, el engine no elige tema random — usa `weekly_focus` obligatorio o un tema genérico. Si no hay weekly_focus, el portal elige un tema por rotación y se lo pasa al motor como `tema_elegido` |
| `weekly_focus` | text, nullable | Instrucción puntual de esta semana (ej. "destacar la venta que cerramos el martes"). Si está cargado, reemplaza la elección aleatoria de tema |
| `weekly_focus_expires_at` | date, nullable | El portal la limpia sola después de la próxima generación — el engine no la borra, solo la lee |
| `drive_folder_id` | text | Carpeta del cliente dentro de la Unidad Compartida |
| `logo_url` | text, nullable | Ya existe como concepto en `main.py` (`cuan_logo.png`); acá es una URL |
| `calendly_link` | text, nullable | Ya existía en `CONFIG` de `main.py`, ahora por cliente |
| `prob_link` | float, default 0 | Ídem |
| `weekly_format` | text, nullable | Formato de la próxima generación (clave de app/engine/formatos.py). null = arco por defecto. Se consume en la generación y vuelve a null |
| `voice_style` | text, nullable | Estilo de voz del cliente (clave de app/engine/voces.py). null = Estándar, el tono de siempre. No se consume |
| `learned_edits` | jsonb, default `[]` | Fase 4: correcciones del equipo `[{id, antes, despues, fecha}]`, la más nueva primero, como máximo 8. El motor las recibe como `aprendizajes` |
| `website_url` | text, nullable | Web del negocio. Se edita en Ajustes (solo sintaxis http/https, sin credenciales) y es el respaldo de `POST /portal/clientes/{id}/marca/proponer` cuando no se manda `web` |

**`client_images`** (nueva) — tracking de no-repetición:
| Campo | Tipo |
|---|---|
| `id` | uuid pk |
| `client_id` | uuid fk |
| `drive_file_id` | text |
| `drive_file_name` | text |
| `last_used_at` | timestamptz, nullable |
| `times_used` | int, default 0 |

**`employee_clients`** (nueva) — asignación PM↔cliente:
| Campo | Tipo |
|---|---|
| `employee_id` | uuid fk |
| `client_id` | uuid fk |

**`client_users`** (nueva, a81) — usuarios del cliente que entran a su panel `/app`:
| Campo | Tipo |
|---|---|
| `id` | uuid pk |
| `client_id` | uuid fk → clients |
| `email` | text — único en toda la base (`lower(email)`): un email pertenece a un solo cliente |
| `name` | text, nullable |
| `role` | text — `'owner'` \| `'editor'` |
| `active` | boolean — `false` corta la sesión al instante |
| `last_login_at` | timestamptz, nullable |
| `created_at` | timestamptz |

**`client_login_links`** (nueva, a81) — links de acceso de un solo uso:
| Campo | Tipo |
|---|---|
| `id` | uuid pk |
| `client_user_id` | uuid fk → client_users |
| `token_hash` | text único — sha256 del token; el token en claro nunca se guarda |
| `expires_at` | timestamptz |
| `used_at` | timestamptz, nullable |
| `created_by` | uuid fk → employees, nullable |
| `created_at` | timestamptz |

**`prompt_history`** (nueva, recomendada) — auditoría del prompt evolutivo:
| Campo | Tipo |
|---|---|
| `id` | uuid pk |
| `client_id` | uuid fk |
| `changed_by` | uuid fk → employees |
| `field` | text — `'business_description'` \| `'weekly_focus'` \| `'tone_examples'` \| `'topics'` |
| `old_value` | text |
| `new_value` | text |
| `changed_at` | timestamptz |

---

## 3. Modelos de datos del contrato (viven en `app/engine/schemas.py`, Pydantic)

```python
class ClientContentConfig(BaseModel):
    client_id: str
    nombre_negocio: str
    business_description: str
    weekly_focus: str | None = None
    tone_examples: list[list[str]]      # cada item: las 4 historias de un hilo de ejemplo
    topics: list[str] | None = None
    logo_url: str | None = None
    calendly_link: str | None = None
    prob_link: float = 0.0              # probabilidad (0-1) de agregar el CTA en la historia 4, sorteada por el motor
    weekly_format: str | None = None
    voice_style: str | None = None      # clave de voces.VOCES; None = tono de siempre
    aprendizajes: list[dict] = []       # fase 4: [{antes, despues}] correcciones del equipo, la más nueva primero
    tema_elegido: str | None = None     # tema de la semana elegido por el portal (solo si no hay weekly_focus); el motor lo prefiere al random

class ImagenCandidata(BaseModel):
    drive_file_id: str
    drive_file_name: str
    image_bytes: bytes                  # el portal ya la bajó de Drive

class ImagenEditada(BaseModel):
    url: str                            # URL nueva de Cloudinary de la imagen compuesta
    public_id: str                      # public_id del asset para limpieza ante una falla posterior

class HiloGenerado(BaseModel):
    historias: list[str]                # 4 textos, en orden
    imagenes_originales_url: list[str]  # Cloudinary, SIN texto (para re-editar después)
    imagenes_editadas_url: list[str]    # Cloudinary, CON texto
    drive_file_ids_usados: list[str]    # el portal marca estos como last_used_at = ahora en client_images
    cta_agregado: bool = False          # si el motor efectivamente sorteó y dibujó el CTA (el portal NO debe re-sortear esto)

class PropuestaMarca(BaseModel):
    business_description: str           # hasta 500 caracteres
    tone_examples: list[list[str]]      # hasta 3 hilos de 4 historias (cada una hasta 140 caracteres)
    topics: list[str]                   # hasta 8 temas cortos

class ResultadoPublicacion(BaseModel):
    ok: bool
    ig_media_id: str | None = None
    error: str | None = None

class ContextoCM(BaseModel):            # CM con IA (sección 16): lo que cambia entre turnos
    hoy: str                            # fecha ISO, America/Argentina/Buenos_Aires
    semana: str                         # resumen compacto de la semana, armado por el portal
    acciones_decididas: list[dict] = [] # [{id, tipo, estado}] desde el último turno de la persona

class InicioCM(BaseModel):
    system: str                         # system prompt congelado de la conversación
    version_herramientas: str           # clave de HERRAMIENTAS_CM, congelada igual

class LlamadaHerramienta(BaseModel):
    id: str                             # id del tool_use
    name: str
    input: dict

class TurnoCM(BaseModel):
    texto: str                          # solo bloques text, para mostrar
    llamadas: list[LlamadaHerramienta]  # vacía si ejecutable es False
    stop_reason: str
    usage: dict
    content_blocks: list[dict]          # bloques crudos: el portal los persiste y reenvía SIN cambios
    ejecutable: bool = True             # False si stop_reason es max_tokens/refusal/…: no correr herramientas
```

## 4. Funciones públicas del motor (`app/engine/content.py`)

```python
def generar_hilo(config: ClientContentConfig, imagenes: list[ImagenCandidata]) -> HiloGenerado:
    """
    imagenes ya viene FILTRADO por no-repetición — el engine no sabe nada de
    "qué se usó hace 3 semanas", eso lo decide el portal antes de llamar acá.
    Debe recibir exactamente 4 imágenes. No escribe en ningún lado — devuelve
    los datos y el portal los persiste.
    Puede levantar EngineError si Claude falla o si faltan imágenes.
    """

def editar_historia(config: ClientContentConfig, imagen_original_bytes: bytes,
                     texto_nuevo: str, num_historia: int) -> ImagenEditada:
    """
    Re-edita UNA historia a partir de su imagen SIN texto (nunca de la ya editada).
    Devuelve un ImagenEditada(url, public_id); el public_id permite borrar el
    asset de Cloudinary si un paso posterior falla.
    """

def generar_texto_de_prueba(config: ClientContentConfig, imagen: ImagenCandidata) -> list[str]:
    """
    Para el botón "Probar prompt" del panel: genera las 4 historias de texto
    pero NO sube nada a Cloudinary ni gasta nada más — solo texto, para que el
    PM vea el efecto de su edición antes de que salga en vivo.
    """

def reescribir_historia(config: ClientContentConfig, texto_actual: str, accion: str,
                        instruccion: str | None = None) -> list[str]:
    """
    Editar con IA (fase 2): hasta 3 versiones nuevas de UNA historia, para que la
    persona elija. `accion` es una clave de ACCIONES_VARIANTE ("corto", "vendedor",
    "cercano", "otra") o "libre", que usa `instruccion`. Solo texto: no manda
    imagen, no sube ni guarda nada. Mantiene la voz del cliente y no inventa datos.
    Levanta ValueError si la acción no existe y ClaudeGenerationError si falla.
    """

def proponer_marca(nombre_negocio: str, texto_web: str | None = None,
                   notas: str | None = None) -> PropuestaMarca:
    """
    Onboarding con IA (fase 5): un borrador de la marca (descripción, ejemplos de
    tono y temas) a partir del texto de la web del negocio y/o lo que cuenta la
    persona. Solo texto: no baja la web (el portal se la pasa ya leída), no sube
    ni guarda nada. No inventa precios, fechas ni cifras.
    Levanta ValueError si no viene ni texto_web ni notas y ClaudeGenerationError
    si Claude falla o no devuelve una propuesta usable.
    """

def publicar_historia(image_url: str, instagram_account_id: str, meta_access_token: str,
                       agregar_cta: bool, calendly_link: str | None) -> ResultadoPublicacion:
    """
    Publica UNA historia en Instagram. No decide cuándo — eso es de Celery/portal,
    que llama a esto una vez por historia del hilo del día.
    """

def iniciar_conversacion_cm(config: ClientContentConfig) -> InicioCM:
    """
    CM con IA (sección 16). System prompt congelado de una conversación nueva (persona,
    reglas, marca, tono y aprendizajes; todo texto del cliente va delimitado como dato no
    confiable) y la versión de herramientas. Determinista. El portal lo guarda y lo reusa
    siempre igual.
    """

def armar_turno_usuario(contexto: ContextoCM | None, texto: str, resultados: list[dict]) -> dict:
    """
    Mensaje de rol user para guardar tal cual y reenviar sin cambios: los tool_result
    pendientes (`{tool_use_id, content, is_error?}`) primero y, si hay texto, un bloque
    `<contexto_actual>` seguido del texto de la persona. Con texto vacío solo devuelve los
    resultados (continuación del bucle). ValueError si no hay ni texto ni resultados.
    """

def turno_cm(system: str, mensajes: list[dict], version_herramientas: str) -> TurnoCM:
    """
    UNA llamada a Claude con el historial (el bucle es del portal). No ejecuta herramientas
    ni toca la base. Devuelve texto, llamadas y los bloques crudos de la respuesta. Si
    stop_reason es max_tokens o refusal, `llamadas` viene vacía y `ejecutable` False.
    ValueError si la versión de herramientas no existe; ClaudeGenerationError si la API
    falla. No reintenta: el cliente va con `max_retries=0` y un techo de 60 s (`CM_TIMEOUT_SECONDS`), por debajo del `busy_until` de 90 s. Los `tool_result` que arma `armar_turno_usuario` van envueltos en `<datos_no_confiables>`.
    """
```

## 5. Quién hace qué, en la práctica

- El **portal** (Codex) consulta Drive, cruza contra `client_images.last_used_at` (excluye lo usado en las últimas N semanas, configurable), arma la lista de 4 `ImagenCandidata` y se la pasa al engine.
- El **engine** (Claude) nunca decide qué imagen usar por repetición — solo genera contenido con lo que le pasan.
- El **portal** escribe en `story_groups`/`stories`/`client_images`/`prompt_history` después de llamar al engine.
- El **portal** decide CUÁNDO llamar cada función: Celery beat semanal para `generar_hilo` (una vez por cliente activo, viernes), Celery task diaria para `publicar_historia` (una vez por historia pendiente del día).
- El **engine** son funciones sin estado — se pueden llamar en cualquier momento y con el mismo input dan el mismo resultado, salvo la llamada real a Claude (no determinística por diseño).
- Errores: el engine levanta excepciones tipadas (`EngineError` y subclases — `ClaudeGenerationError`, `ImageProcessingError`, `MetaPublishError`); el portal decide cómo reintentar/loguear, el engine no hace retry ni logging propio de negocio (sí puede loguear técnico a stdout).

## 6. Reglas de trabajo en git

- Worktree/rama separada por zona: `app/engine/**` es de Claude/Gonza, todo lo demás de Codex/Matías.
- PRs chicos. Cross-review recomendado: Codex revisa los PRs que tocan `app/engine`, y viceversa con lo que arme Codex — dos modelos revisando atrapan más que uno solo mirando su propio trabajo.
- Cualquier cambio de firma/tabla de este documento se negocia y se actualiza acá ANTES de escribir el código, no al revés.

## 7. Extensión del portal — Bloque A2

- La base existente usa `clients.active` y `clients.name`: el portal filtra `active = true` y mapea `name` a `ClientContentConfig.nombre_negocio`.
- `clients.generation_error` (text nullable) y `generation_error_at` (timestamptz nullable) exponen el último error de generación para B3; una generación persistida los limpia.
- `story_groups.status` admite también `pending`. `generation_week` (date nullable) identifica la semana generada; es única por cliente cuando está cargada, sin afectar grupos manuales.
- `stories` agrega `image_original_url` (text nullable), `fecha_publicacion` (date nullable), `estado` (text nullable: `pendiente`, `publicando`, `publicado`, `error`, `cancelada`), `error` (text nullable), `agregar_cta` (boolean default false) y `published_at` (timestamptz nullable). `cancelada` implementa el borrado blando desde el portal. Las filas anteriores conservan estado nulo y siguen el flujo existente.
- El hilo semanal tiene cuatro historias en orden 1–4, cada una con su propio `fecha_publicacion` repartido en la semana siguiente a la generación (lunes/miércoles/viernes/domingo — `PUBLISH_DAY_OFFSETS` en `content_jobs.py`), no las cuatro el mismo día. `story_groups.scheduled_date` guarda el primer día (el lunes) solo para agrupar/ordenar por semana. La publicación diaria corre a las 09:00 por defecto, configurable por entorno; todos los horarios usan America/Argentina/Buenos_Aires.
- La RPC `persist_generated_thread` (solo service_role) guarda grupo, cuatro historias y uso de imágenes en una transacción. Incrementa `times_used` atómicamente y limpia el foco solo si aún coincide con el texto y vencimiento utilizados; no consume un foco reemplazado durante la generación.
- `publicando` es un reclamo atómico antes de llamar a Meta, para evitar publicaciones duplicadas por workers concurrentes. Los errores no se reintentan automáticamente; un reclamo interrumpido requiere revisión manual antes de volver a pendiente.
- A2 toma las primeras cuatro imágenes de Drive. La lectura del historial y el filtro de no-repetición quedan para A3.
- El portal reordena historias mediante la RPC backend-only `reorder_stories`, que difiere la unicidad `(story_group_id, order)` durante el intercambio y valida que todas las filas pertenezcan al mismo grupo.
- El portal actualiza campos de prompt mediante la RPC backend-only `update_client_prompt`, que valida empleado y cliente contra la agencia esperada y guarda la actualización junto con sus filas de `prompt_history` en una única transacción.

## 8. Panel del cliente — a81

- El cliente usa su propio panel en `/app`. La consola interna del equipo sigue en `/portal`, sin cambios de comportamiento.
- La consola genera el acceso con `POST /portal/clientes/{id}/acceso` (`{email, name?}` → `{email, name, url, expires_at}`). El link (`/entrar?token=...`) vale `client_link_days` (7 por defecto), sirve una sola vez y el equipo lo manda a mano (por ejemplo, WhatsApp). `GET` lista los accesos y `DELETE .../acceso/{user_id}` los desactiva (y gasta los links pendientes de ese usuario).
- `GET /entrar?token=...` solo muestra el botón "Entrar a mi panel", sin tocar la base, porque las vistas previas de WhatsApp o de los mails no tienen que gastar el link. El canje es `POST /entrar`. Es atómico (`update ... where used_at is null`) y responde siempre el mismo mensaje genérico, sea cual sea el motivo del fallo.
- La sesión del cliente usa la cookie `client_session` (httponly, samesite lax y `secure` en producción) y dura `client_session_days` (30). Es distinta de `session`, la de empleados: ninguna autentica a la otra. `require_client` acepta el Bearer o `client_session`, exige `role == "client"` y en cada request lee `client_users` junto con `clients(agency_id,active)`. Si el usuario o el cliente están inactivos, responde 401.
- API del cliente en `/app/api`: `GET /me`, `GET /semana`, `PATCH /historias/{id}/aprobar`, `PATCH /dias/{fecha}/aprobar` y `PATCH /semana` (`{enfoque, formato}` → `clients.weekly_focus` / `weekly_format`). **El `client_id` sale siempre de la sesión, nunca de la URL ni del body, y lo ajeno responde 404 igual que lo inexistente.** Como la base se usa con service role, este filtro es lo único que aísla a un cliente de otro.
- Aprobar desde el panel = `stories.aprobado`, que es lo único que habilita la publicación. El panel no toca `agendado`. `GET /app/salir` cierra la sesión del cliente.

## 9. Formatos, estilo de voz y formato por día — a82

- `app/engine/formatos.py` trae 9 formatos: los 6 de a78 más `cuenta_regresiva`, `teaser` y `resultado`, para lanzamientos. Cada formato tiene `label`, `hint`, `arco` (4 consignas, máx 10/8/8/10 palabras) y `unica`: una oración que dice cómo se cuenta ese formato cuando es una sola historia. Ninguna consigna inventa fechas, horas ni cifras; solo las usa si figuran en el tema.
- `app/engine/voces.py` define `VOCES` (`canchero`, `tecnico`, `sobrio`). Cada estilo reemplaza las dos viñetas de vocabulario de REGLAS DE TONO en `_armar_prompt` y `_armar_prompt_historia_unica`. Si `voice_style` es null o una clave desconocida, el prompt queda idéntico al de antes (`_REGLAS_TONO_POR_DEFECTO`).
- En la consola: `GET /portal/estilos-voz` devuelve `[{key, label, hint}]` y `PATCH /portal/clientes/{id}/estilo-voz` recibe `{voice_style}` (clave o null; una clave desconocida da 422). El panel del cliente todavía no edita el estilo de voz.
- `POST /portal/clientes/{id}/historias/generar` acepta dos campos opcionales: `formato`, una clave de `FORMATOS` (otra da 422), y `tema`, de hasta 200 caracteres (vacío = null). Llegan a `generar_texto_para_imagen(config, image_bytes, formato=None, tema=None)`. Con tema, el tema manda y la foto se elige primero entre las que coinciden por nombre o subcarpeta (`content_jobs.priorizar_por_tema`, sin visión). Si no viene ninguno de los dos, el endpoint y el prompt quedan como antes.

## 10. Dos paneles: agencia multi-cliente y cliente directo — a83

- `agencies.mode` (text, default `'multi'`, check `multi`/`direct`). `multi` es la consola de siempre: varios clientes, PMs y empleados. `direct` es un negocio que usa Storias solo: una agencia con UN cliente.
- El modo viaja en el JWT del empleado (`agency_mode`, que lo lee `auth._agency_mode` al hacer login con Google). Si falla la lectura o la columna no está migrada, queda `multi`. Después de cambiar el modo hay que volver a entrar. `GET /portal/me` lo devuelve.
- La consola es la misma (`/portal`, `portal.js`). En modo directo esconde (`html.direct-mode .multi-only`) la lista de clientes, búsqueda y filtros, Inicio, PM asignado, asignar PM, alta masiva y Acceso del cliente. Abre directo la ficha del único negocio y "Clientes" pasa a llamarse "Mi negocio".
- El backend también lo hace cumplir: en modo directo `GET /portal/empleados`, `PATCH /clientes/{id}/pm`, `POST /asignar-pm` y `/clientes/{id}/acceso` responden 403 "No disponible en el panel de cliente directo". `POST /clientes/alta-masiva` solo crea el primer y único cliente. El aislamiento entre agencias sigue siendo el de siempre (`_client_or_error`, 403 fuera de la agencia).
- Alta de un cliente directo (a mano, por ahora): una fila en `agencies` con `mode = 'direct'`, el dueño en `employees` con `role = 'admin'` (entra con Google) y su único cliente en `clients`. Los INSERT de ejemplo están en `migrations/20261022_a83_agency_mode.sql`.
- El panel `/app` de a81 sigue disponible para clientes de una agencia multi que solo revisan y aprueban.

## 11. Diseño de la consola, fase 1

- La ficha de un cliente se divide en cuatro secciones, a las que se entra desde el menú lateral (`data-client-tab`):
  - **Semana**: qué destacar (enfoque y formato), el plan, las historias y el calendario.
  - **Marca**: voz, temas, estilo de voz, tipografía, ejemplos de tono, CTA y logo.
  - **Historial**: lo publicado y los cambios de la marca.
  - **Ajustes**: Drive, Instagram y contacto; en modo multi también el PM y el acceso del cliente.
- El modal "Ajustes del cliente" ya no existe: `openSettings()` abre la sección Ajustes. Los ids de los campos no cambiaron.
- En modo multi, las secciones aparecen debajo de "Clientes", con el nombre del cliente elegido. En modo directo son el menú principal. La última sección abierta se recuerda en el navegador (`storias.clientTab`).

## 12. Editar con IA, fase 2

- `POST /portal/historias/{id}/variantes` recibe `{accion, instruccion?, texto?}` y devuelve `{variantes: [...]}`. `texto` es lo que está escrito en el editor (puede no estar guardado todavía); si no viene, se usa el texto guardado. No escribe en la base. Responde 409 si la historia ya está publicada o cancelada, 422 si no tiene texto, si la acción no existe o si la IA falla, y 403 si es de otra agencia.
- En el editor de la historia aparece el panel "Editar con IA", con cuatro botones (Más corto, Más vendedor, Más cercano, Otra idea) y un pedido libre. Elegir una opción solo llena el editor: "Guardar historia" la compone sobre la imagen con el flujo de siempre. También se puede volver al texto anterior.
- En Marca, "Estilo de las historias" queda siempre desplegado.

## 13. Fotos, fase 3

- La sección "Fotos" del menú muestra las fotos de la carpeta de Drive del cliente (un nivel de subcarpetas), con lo que reconoció la IA (`image_tags`: producto, escena y persona) y su uso (`client_images`).
- Cada foto tiene un estado: `nueva` (nunca se usó), `disponible` (ya pasaron las `NO_REPEAT_WEEKS` semanas) o `reciente` (se usó hace poco y la IA no la repite).
- `GET /portal/clientes/{id}/fotos` devuelve `{carpeta, fotos: [{id, nombre, carpeta, analizada, producto, escena, persona, veces_usada, ultima_vez, estado}], semanas_sin_repetir}`. Si Drive falla, responde 422.
- `GET /portal/clientes/{id}/fotos/{file_id}/miniatura` sirve un JPEG chico a través del server, porque el navegador no puede pedirle fotos privadas a Drive. Primero usa la miniatura del propio Drive y, si no hay, baja la original y la achica (`drive.thumbnail`). **Solo sirve archivos que están en la carpeta de ese cliente**: la cuenta de servicio puede leer las carpetas de todos, así que el server valida contra el listado de la carpeta (cacheado 10 min). Las miniaturas quedan en memoria (hasta 400).
- `PATCH /portal/clientes/{id}/fotos/{file_id}` con `{producto}` corrige el producto (`image_tags.subject`). Vacío = sin producto. Si la IA todavía no analizó la foto, responde 409. Esa corrección es la que usa la selección de fotos del hilo semanal.

## 14. Aprendizaje, fase 4

- Cuando alguien cambia el texto de una historia (`PATCH /portal/historias/{id}`), el portal guarda el cambio en `clients.learned_edits`. Si no había texto antes o el cambio es solo de espacios o mayúsculas, no guarda nada. Si ese mismo texto ya se había corregido antes, la corrección nueva reemplaza a la vieja. Es best-effort: si falla, la historia se guarda igual.
- `build_content_config` pasa esas correcciones al motor como `ClientContentConfig.aprendizajes`. El motor arma un bloque "Así corrigió el equipo de {negocio}…" (`_bloque_aprendizajes`, hasta 8, cada texto recortado a 140 caracteres) después de los ejemplos de tono, en los tres prompts: el hilo, la historia suelta y Editar con IA. Si no hay correcciones, los prompts quedan idénticos a los de antes.
- En Marca, el panel "Lo que la IA aprendió de tus cambios" muestra cada corrección y deja olvidarla. Endpoints: `GET /portal/clientes/{id}/aprendizajes`, `DELETE .../aprendizajes/{item_id}` (404 si no existe) y `DELETE .../aprendizajes` para olvidar todo.
- Migración: `migrations/20261023_f4_learned_edits.sql`. Hay que aplicarla antes de desplegar, porque `_CLIENT_DETAIL` pide la columna.

## 15. Onboarding con IA, fase 5

- `POST /portal/clientes/{id}/marca/proponer` recibe `{web?, notas?}` (web hasta 300 caracteres, notas hasta 1500; al menos uno) y devuelve `{business_description, tone_examples, topics, aviso}`. No escribe en la base: la persona revisa el borrador y lo guarda con el `PATCH /portal/clientes/{id}` de siempre, así que queda en `prompt_history`.
- El portal baja la web (`app/services/web_text.py`) y le pasa al motor solo el texto. Solo acepta http/https, rechaza hosts que resuelven a IPs privadas, loopback, link-local o reservadas (también en cada redirección, hasta 3), corta a los 8 segundos y a 1,5 MB, y solo lee HTML o texto. Si la web no se puede leer pero hay notas, sigue con las notas y devuelve el motivo en `aviso`; si no hay notas, responde 422.
- El motor (`content.proponer_marca`) devuelve una `PropuestaMarca` ya recortada a los límites que valida `ClientPatch`: descripción de hasta 500 caracteres, hasta 3 ejemplos de 4 historias de hasta 140 y hasta 8 temas.
- En Marca, el panel "Armá la marca con IA" está abierto si falta la descripción, los ejemplos o los temas, y plegado si no. El aviso "Falta completar" de Semana tiene un botón que lleva ahí. En el borrador, la descripción y los temas reemplazan lo actual y los ejemplos se suman a los que ya había (hasta 6); cada parte se puede destildar antes de guardar.
- Si el pedido no trae `web`, el portal usa `clients.website_url` (Ajustes). El panel "Armá la marca con IA" precarga ese valor; proponer con otra web no lo guarda: guardarlo es explícito, en Ajustes.

## 16. CM con IA — conversación con el equipo

Un chat dentro de la ficha del cliente: el CM con IA lee la marca, la semana, las fotos y el historial, y **propone** cambios. Nunca escribe solo: toda propuesta queda pendiente hasta que una persona la aprueba. v1 es solo para empleados de la consola (incluido el dueño en modo directo), no para el panel `/app`. Hay varias conversaciones por cliente.

**Engine (`app/engine/cm.py`, `app/engine/cm_tools.py`)** — funciones puras: una llamada a Claude por turno, sin base, sin Drive, sin ejecutar herramientas, sin retry ni logging de negocio.
- `iniciar_conversacion_cm(config) -> InicioCM`, `armar_turno_usuario(contexto, texto, resultados) -> dict` y `turno_cm(system, mensajes, version_herramientas) -> TurnoCM`. Firmas y esquemas (`ContextoCM`, `InicioCM`, `LlamadaHerramienta`, `TurnoCM`) en las secciones 3 y 4.
- El `system` y la `version_herramientas` se congelan al crear la conversación (`cm_conversations.system_prompt`, `.tools_version`). Todo lo que cambia (fecha, semana, acciones ya decididas) viaja en el turno de la persona dentro de `<contexto_actual>`, que se guarda tal cual y se reenvía igual. Así el prefijo cacheado y los bloques de razonamiento no cambian.
- `TurnoCM.content_blocks` son los bloques crudos de la respuesta: el portal los persiste (jsonb) y los reenvía SIN filtrar, reordenar ni truncar (incluidos `thinking` / `redacted_thinking`). Al portal le sirven `texto` para mostrar y `llamadas` para ejecutar.
- Si `stop_reason` es `max_tokens` o `refusal`, `llamadas` viene vacía y `ejecutable` en `False`: el portal no corre ninguna herramienta, guarda la fila con `replay = false` (solo auditoría) y devuelve un error.
- Claude: `CLAUDE_MODEL`, `thinking = {"type": "adaptive"}` (nunca `budget_tokens`), `output_config = {"effort": "medium"}`, `max_tokens = 4000`, sin `tool_choice` forzado, cache en el system y en el último bloque del último mensaje (sobre una copia).
- Todo texto que viene de afuera (marca, web, nombres de fotos, historias) va dentro de `<datos_no_confiables origen="...">` y el prompt lo trata como dato, nunca como instrucción.

**Herramientas `HERRAMIENTAS_CM["v1"]`** — ordenadas por nombre, `strict: True`, `additionalProperties: False`. Ninguna recibe `client_id`, `agency_id` ni URL: el portal las ata al cliente de la conversación.
- Lectura: `leer_marca`, `leer_semana`, `leer_historial`, `leer_fotos` (`solo_disponibles`) y `leer_web` (sin argumentos: lee `clients.website_url` con las defensas SSRF de `web_text`).
- Propuesta (no escriben): `proponer_descripcion {descripcion, motivo}`, `proponer_temas {temas, motivo}`, `proponer_ejemplo_tono {historias, motivo}`, `proponer_enfoque_semana {enfoque, formato}` (`formato` = clave de `FORMATOS` o vacío) y `proponer_texto_historia {historia_id, texto, motivo}`. Los límites (500 caracteres, 8 temas, 4 historias de 140…) los valida el portal.

**Tablas (las crea el lado portal; migración planeada `migrations/20261111_cm_agent.sql`)** — RLS activado y sin políticas: solo service role.
- `agencies.cm_enabled` (boolean, default false) y `Settings.cm_enabled` (env). Apagado = 404 en todos los endpoints del CM.
- `cm_conversations`: `id`, `client_id`, `created_by`, `title`, `system_prompt`, `tools_version`, `busy_until` (reclamo de 90 s contra concurrencia), `user_turns`, `created_at`, `updated_at`, `closed_at`.
- `cm_messages`: `id`, `conversation_id`, `client_id`, `seq` (único por conversación), `role` (`user`/`assistant`), `kind` (`mensaje`/`tool_result`/`respuesta`), `content` (jsonb: los bloques tal cual), `replay` (false = solo auditoría), `usage`, `stop_reason`, `created_by`, `created_at`.
- `cm_pending_actions`: `id`, `conversation_id`, `client_id`, `tool_use_id` (único por conversación), `kind`, `payload`, `before` (foto del valor al proponer), `status` (`pending`, `applying`, `approved`, `discarded`, `stale`, `failed`), `error`, `decided_by`, `decided_at`, `created_at`.

**Reglas de aprobación (portal)**
- Las herramientas `proponer_*` solo insertan una fila `pending` en `cm_pending_actions`; el resultado que vuelve al modelo es "propuesta creada, esperando aprobación". Lo que diga un texto de la web, de una foto o de una historia nunca ejecuta una escritura.
- Aprobar: reclamo atómico `pending → applying` (`update … where status = 'pending'`, un segundo intento da 409), revalidar agencia, cliente activo y estado de la historia, comparar `before` con el valor actual (si cambió: `stale`) y aplicar con los MISMOS servicios que la edición manual (el texto de una historia vuelve a componer la imagen y alimenta `learned_edits`). Resultado: `approved` o `failed` con detalle. `decided_by` es el empleado que aprobó.
- El `client_id` sale siempre de la conversación; una conversación o acción de otra agencia responde 404. Una `historia_id` que no es del cliente de la conversación es un `is_error` para el modelo.
- El resultado de las acciones decididas viaja en `acciones_decididas` del siguiente turno.
- Límites (`Settings`): 6 llamadas a Claude por mensaje, 5 herramientas por llamada (las extras reciben `is_error`), 40 turnos de la persona por conversación, 60 por cliente por día, texto de hasta 2000 caracteres. Un fallo de Claude da 422 y conserva el mensaje de la persona.

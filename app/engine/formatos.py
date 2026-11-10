"""Formatos de la semana: cómo se cuentan las 4 historias de un hilo.

Cada formato reemplaza las 4 consignas del arco por defecto de
content._armar_prompt. `{negocio}` se reemplaza por el nombre del negocio.
Clave None / desconocida = arco por defecto (el de siempre).

`unica` es una sola oración que dice cómo se cuenta el formato cuando es UNA
historia suelta (content._armar_prompt_historia_unica, a82). Ninguna consigna
inventa fechas, horas, cifras ni precios: solo los usa si figuran en el tema.
"""

FORMATOS: dict[str, dict] = {
    "lanzamiento": {
        "label": "Producto nuevo",
        "hint": "Presenta algo que acaba de llegar.",
        "arco": [
            "anuncia el producto nuevo con un gancho que genere curiosidad (máx 10 palabras)",
            "el problema o la molestia que resuelve (máx 8 palabras)",
            "el beneficio principal y por qué es distinto (máx 8 palabras)",
            "CTA con una palabra clave en mayúsculas para escribir y pedir más info (máx 10 palabras)",
        ],
        "unica": "Presentá el producto nuevo con un gancho que genere curiosidad por lo que resuelve.",
    },
    "capsula": {
        "label": "Tip o dato útil",
        "hint": "Una función o dato que casi nadie conoce.",
        "arco": [
            "un dato o función que casi nadie conoce, con gancho (máx 10 palabras)",
            "el dato concreto, explicado simple (máx 8 palabras)",
            "cómo cambia el día a día de quien lo usa (máx 8 palabras)",
            "CTA con una palabra clave en mayúsculas para consultar o pedir ayuda (máx 10 palabras)",
        ],
        "unica": "Contá un dato o una función que casi nadie conoce, explicado simple.",
    },
    "comparativa": {
        "label": "Comparativa",
        "hint": "Enfrenta dos opciones.",
        "arco": [
            "una pregunta que enfrenta dos opciones, la común contra la de {negocio} (máx 10 palabras)",
            "lo que pasa con la opción común (máx 8 palabras)",
            "lo que logra la opción de {negocio} (máx 8 palabras)",
            "CTA con una palabra clave en mayúsculas para escribir y elegir (máx 10 palabras)",
        ],
        "unica": "Enfrentá la opción común con la de {negocio} y mostrá qué cambia.",
    },
    "prueba_social": {
        "label": "Venta o reseña",
        "hint": "Cuenta una venta cerrada o lo que dijo un cliente.",
        "arco": [
            "anuncia una venta cerrada o lo que dijo un cliente, con gancho humano (máx 10 palabras)",
            "la situación o necesidad que tenía esa persona (máx 8 palabras)",
            "el resultado que consiguió gracias a {negocio} (máx 8 palabras)",
            "CTA con una palabra clave en mayúsculas invitando a ser el próximo (máx 10 palabras)",
        ],
        "unica": "Contá una venta cerrada o lo que dijo un cliente, con un gancho humano.",
    },
    "promo": {
        "label": "Promo con urgencia",
        "hint": "Oferta, cuotas o descuento por tiempo limitado.",
        "arco": [
            "avisa la promo con urgencia (máx 10 palabras)",
            "qué incluye o qué se lleva el cliente (máx 8 palabras)",
            "hasta cuándo o por qué conviene aprovecharla ahora (máx 8 palabras)",
            "CTA con una palabra clave en mayúsculas para escribir y comprar (máx 10 palabras)",
        ],
        "unica": "Avisá la promo con urgencia, con plazos o condiciones solo si figuran en el tema.",
    },
    "detras": {
        "label": "Detrás de escena",
        "hint": "Muestra cómo trabaja el equipo por dentro.",
        "arco": [
            "invita a mirar cómo trabajan por dentro (máx 10 palabras)",
            "qué hacen en el día a día (máx 8 palabras)",
            "por qué lo hacen así: el cuidado que diferencia a {negocio} (máx 8 palabras)",
            "CTA con una palabra clave en mayúsculas para escribir y conocernos (máx 10 palabras)",
        ],
        "unica": "Mostrá cómo trabaja el equipo por dentro y el cuidado que diferencia a {negocio}.",
    },
    "cuenta_regresiva": {
        "label": "Cuenta regresiva",
        "hint": "Avisa que falta poco para un lanzamiento o apertura.",
        "arco": [
            "avisa que falta poco, con el momento que figure en el tema; si no figura, 'muy pronto' (máx 10 palabras)",
            "qué va a pasar o qué se abre, sin spoilear todo (máx 8 palabras)",
            "por qué conviene estar atento o ser de los primeros (máx 8 palabras)",
            "CTA con una palabra clave en mayúsculas para anotarse o activar el aviso (máx 10 palabras)",
        ],
        "unica": "Avisá que falta poco para lo que figure en el tema, con el momento solo si está en el tema.",
    },
    "teaser": {
        "label": "Teaser",
        "hint": "Adelanta algo sin contarlo todo.",
        "arco": [
            "una pista que genera curiosidad sobre algo que viene (máx 10 palabras)",
            "un detalle concreto, sin revelar el resto (máx 8 palabras)",
            "por qué {negocio} lo prepara y a quién le va a servir (máx 8 palabras)",
            "CTA con una palabra clave en mayúsculas para enterarse primero (máx 10 palabras)",
        ],
        "unica": "Dejá una pista que genere curiosidad sobre lo que viene, sin revelarlo todo.",
    },
    "resultado": {
        "label": "Resultado de lanzamiento",
        "hint": "Cuenta cómo salió algo que se lanzó.",
        "arco": [
            "cuenta el resultado de un lanzamiento que figure en el tema, con gancho (máx 10 palabras)",
            "el número o el hecho concreto que lo muestra, solo si figura en el tema (máx 8 palabras)",
            "qué significa para quienes participaron (máx 8 palabras)",
            "CTA con una palabra clave en mayúsculas para sumarse a la próxima (máx 10 palabras)",
        ],
        "unica": "Contá cómo salió lo que se lanzó, con un dato concreto solo si figura en el tema.",
    },
}

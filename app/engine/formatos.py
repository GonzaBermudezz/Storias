"""Formatos de la semana: cómo se cuentan las 4 historias de un hilo.

Cada formato reemplaza las 4 consignas del arco por defecto de
content._armar_prompt. `{negocio}` se reemplaza por el nombre del negocio.
Clave None / desconocida = arco por defecto (el de siempre).
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
    },
}

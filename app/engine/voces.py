"""Estilos de voz por cliente (a82): cómo suena cada historia.

Cada estilo reemplaza las dos viñetas de vocabulario de REGLAS DE TONO en
content._armar_prompt y content._armar_prompt_historia_unica. Clave None,
vacía o desconocida = el tono de siempre (content._REGLAS_TONO_POR_DEFECTO).
El resto de las reglas (una idea por historia, máximos de palabras, sin
hashtags ni emojis, formato de salida) no depende del estilo.
"""

VOCES: dict[str, dict] = {
    "canchero": {
        "label": "Canchero",
        "hint": "Humor y cercanía, con ironía liviana.",
        "reglas": [
            "Tono de amigo canchero, de vos, con humor, ironía liviana y algo de autocrítica",
            "De vez en cuando un apodo de cariño (\"bella\", \"amor\"), sin insultos ni palabrotas",
            "Vocabulario simple y cotidiano, sin lenguaje corporativo ni frases de gurú motivacional",
            "Nunca prometas resultados garantizados",
        ],
    },
    "tecnico": {
        "label": "Técnico y preciso",
        "hint": "Para un público que ya conoce el rubro.",
        "reglas": [
            "Tono profesional y cercano, de vos",
            "Podés usar los términos del rubro que su público ya conoce, sin explicarlos",
            "Priorizá datos concretos (números, medidas, especificaciones) sobre adjetivos",
            "Sin frases vacías ni exageraciones",
        ],
    },
    "sobrio": {
        "label": "Sobrio",
        "hint": "Pocas palabras, sin humor, respetuoso.",
        "reglas": [
            "Tono sobrio y respetuoso, de vos",
            "Pocas palabras, sin humor ni exclamaciones",
            "Vocabulario simple",
            "Nada de exageraciones ni promesas",
        ],
    },
}

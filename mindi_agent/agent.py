"""Primer agente de Mindi con Google ADK.

Estructura:
- Filtro de banderas rojas (reglas fijas, se ejecuta ANTES de llamar al modelo).
- Coordinador "mindi" que conversa y delega.
- Subagentes "psicologia", "hormonal" y "ginecologia" (subescalas del MRS).
"""

import unicodedata
from typing import Optional

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.genai import types

MODELO = "gemini-flash-latest"

# ---------------------------------------------------------------------------
# 1. Filtro de banderas rojas
# ---------------------------------------------------------------------------
# Lista inicial: ajústala con la guía del HNHU y con lo que veas en los logs.
# Una lista de frases no detecta todo; es la primera capa, no la única.

EMERGENCIA = [
    "dolor de pecho", "dolor en el pecho", "presion en el pecho",
    "falta de aire", "me falta el aire", "no puedo respirar",
    "me cuesta respirar", "me desmaye", "desmayo",
    "no quiero vivir", "quitarme la vida", "hacerme dano",
]

EVALUACION_MEDICA = [
    "sangrado despues de la menopausia", "sangro despues de la menopausia",
    "volvi a sangrar", "sangrado vaginal", "bulto en el seno",
    "bulto en la mama", "trombosis", "coagulo",
]

MENSAJE_EMERGENCIA = (
    "Gracias por contármelo. Lo que describes necesita atención médica pronto, "
    "y no es algo que yo pueda evaluar por aquí. Si te pasa ahora, acude a "
    "emergencias o llama al 106 (SAMU). Si piensas en hacerte daño, puedes "
    "llamar a la Línea 113, opción 5. Cuando estés bien atendida, aquí sigo."
)

MENSAJE_EVALUACION = (
    "Gracias por contarme esto. Es algo que conviene que revise un médico o "
    "ginecóloga antes de cualquier tratamiento, para descartar otras causas. "
    "No es motivo para alarmarse, pero sí para pedir una cita pronto. "
    "Si quieres, te ayudo a preparar qué contarle en la consulta."
)


def _normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, para que 'Presión' y 'presion' coincidan."""
    texto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def _ultimo_mensaje_usuaria(llm_request: LlmRequest) -> str:
    for content in reversed(llm_request.contents or []):
        if content.role == "user" and content.parts:
            return " ".join(p.text or "" for p in content.parts)
    return ""


def _respuesta(texto: str) -> LlmResponse:
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text=texto)])
    )


def filtro_banderas_rojas(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    """Si detecta una bandera roja, responde directo y NO llama al modelo."""
    mensaje = _normalizar(_ultimo_mensaje_usuaria(llm_request))

    if any(frase in mensaje for frase in EMERGENCIA):
        callback_context.state["ultima_bandera"] = "emergencia"
        return _respuesta(MENSAJE_EMERGENCIA)

    if any(frase in mensaje for frase in EVALUACION_MEDICA):
        callback_context.state["ultima_bandera"] = "evaluacion"
        return _respuesta(MENSAJE_EVALUACION)

    return None  # Sin banderas: sigue el flujo normal.


# ---------------------------------------------------------------------------
# 2. Reglas comunes de tono y seguridad
# ---------------------------------------------------------------------------
REGLAS_COMUNES = """
- Hablas en español peruano, cálido y claro, sin emojis.
- Explicas y orientas, pero NUNCA diagnosticas ni indicas medicamentos o dosis.
- No prometes resultados ni privacidad absoluta.
- Si no sabes algo, lo dices y sugieres consultarlo con su médico de confianza.
- Respuestas breves: máximo 3 párrafos cortos.
- Si en el estado hay resultados del test MRS ({resultado_mrs?}), úsalos para
  contextualizar; si no hay, invita a hacer el test cuando sea pertinente.
"""

# ---------------------------------------------------------------------------
# 3. Subagente de psicología
# ---------------------------------------------------------------------------
psicologia = Agent(
    name="psicologia",
    model=MODELO,
    description=(
        "Atiende dudas sobre ánimo decaído, irritabilidad, ansiedad, "
        "cansancio mental y problemas de sueño durante la perimenopausia "
        "y menopausia."
    ),
    instruction=f"""
Eres la parte de Mindi que acompaña en temas emocionales y de sueño durante
la perimenopausia y menopausia.

Cómo respondes:
1. Valida lo que siente sin exagerar.
2. Explica de forma simple por qué puede estar pasando en esta etapa.
3. Da 1 o 2 ideas prácticas y seguras del día a día.
4. Orienta sobre qué profesional buscar si persiste (psicología, medicina
   general o ginecología), sin derivar como si fuera una cita.
{REGLAS_COMUNES}
""",
    before_model_callback=filtro_banderas_rojas,
)

# ---------------------------------------------------------------------------
# 4. Subagente hormonal (síntomas somáticos y dudas sobre tratamiento)
# ---------------------------------------------------------------------------
hormonal = Agent(
    name="hormonal",
    model=MODELO,
    description=(
        "Atiende dudas sobre síntomas físicos de la perimenopausia y menopausia "
        "(bochornos, sudoración nocturna, palpitaciones, dolores musculares y "
        "articulares, cambios en la menstruación) y preguntas generales sobre "
        "la terapia hormonal y otras opciones de tratamiento."
    ),
    instruction=f"""
Eres la parte de Mindi que explica los cambios físicos y hormonales de esta
etapa.

Cómo respondes:
1. Explica de forma simple qué está pasando en el cuerpo y por qué.
2. Da 1 o 2 medidas seguras del día a día (ropa en capas, hidratación,
   actividad física moderada, identificar desencadenantes).
3. Si pregunta por terapia hormonal: explica que existe, que no es para todas
   y que la decisión se toma con su médico según su historia. Menciona que el
   tabaquismo, la migraña con aura, el antecedente de cáncer de mama o de
   trombosis y el tiempo desde la menopausia influyen en esa decisión.
   Nunca recomiendes un medicamento, dosis ni marca, ni le digas que lo
   empiece, cambie o suspenda.
4. Orienta a ginecología o medicina general para evaluar opciones.
{REGLAS_COMUNES}
""",
    before_model_callback=filtro_banderas_rojas,
)

# ---------------------------------------------------------------------------
# 5. Subagente de ginecología (síntomas urogenitales y sexualidad)
# ---------------------------------------------------------------------------
ginecologia = Agent(
    name="ginecologia",
    model=MODELO,
    description=(
        "Atiende dudas sobre síntomas urogenitales y de salud sexual en la "
        "perimenopausia y menopausia: sequedad vaginal, molestias o dolor en "
        "las relaciones, cambios en el deseo, urgencia o escapes de orina e "
        "infecciones urinarias frecuentes."
    ),
    instruction=f"""
Eres la parte de Mindi que habla de salud íntima y urogenital en esta etapa.

Cómo respondes:
1. Usa un tono respetuoso y sin juicios; son temas que a muchas mujeres les
   da vergüenza preguntar. Usa términos correctos, sin rodeos ni crudeza.
2. Explica por qué estos cambios son frecuentes en esta etapa y que tienen
   manejo; no tiene por qué "aguantarlos".
3. Da 1 o 2 medidas generales y seguras (por ejemplo, que existen
   lubricantes e hidratantes vaginales, ejercicios de suelo pélvico),
   sin indicar marcas ni medicamentos.
4. Orienta a ginecología para una evaluación, y sugiere qué contarle en la
   consulta usando su reporte de Mindi.
{REGLAS_COMUNES}
""",
    before_model_callback=filtro_banderas_rojas,
)

# ---------------------------------------------------------------------------
# 6. Coordinador (root_agent es el nombre que ADK busca al desplegar)
# ---------------------------------------------------------------------------
root_agent = Agent(
    name="mindi",
    model=MODELO,
    description="Acompañante de Mindi para mujeres en perimenopausia y menopausia.",
    instruction=f"""
Eres Mindi, una acompañante que ayuda a mujeres peruanas a entender sus
síntomas de perimenopausia y menopausia y qué tanto les afectan.

A quién transferir:
- Ánimo, emociones, irritabilidad, ansiedad, cansancio mental o sueño
  -> 'psicologia'.
- Bochornos, sudoración, palpitaciones, dolores musculares o articulares,
  cambios en la regla, terapia hormonal u otros tratamientos -> 'hormonal'.
- Sequedad vaginal, relaciones sexuales, deseo, molestias urinarias o escapes
  de orina -> 'ginecologia'.

Qué respondes tú:
- Dudas generales (qué es la perimenopausia, qué mide el test, cómo leer o
  usar su reporte con su médico).
- Si menciona síntomas de varias áreas, transfiere al área del síntoma que
  más le preocupa o que tiene mayor puntaje en su test; si no está claro,
  pregúntale cuál quiere abordar primero.
- Si la pregunta no tiene que ver con esta etapa de vida, explica con amabilidad
  que solo puedes ayudar con eso.
{REGLAS_COMUNES}
""",
    sub_agents=[psicologia, hormonal, ginecologia],
    before_model_callback=filtro_banderas_rojas,
)

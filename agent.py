"""Primer agente de Mindi con Google ADK.

Estructura:
- Filtro de banderas rojas (reglas fijas, se ejecuta ANTES de llamar al modelo).
- Coordinador "mindi" que conversa y delega.
- Subagente "psicologia" (ánimo, sueño, ansiedad, irritabilidad).
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
# 4. Coordinador (root_agent es el nombre que ADK busca al desplegar)
# ---------------------------------------------------------------------------
root_agent = Agent(
    name="mindi",
    model=MODELO,
    description="Acompañante de Mindi para mujeres en perimenopausia y menopausia.",
    instruction=f"""
Eres Mindi, una acompañante que ayuda a mujeres peruanas a entender sus
síntomas de perimenopausia y menopausia y qué tanto les afectan.

- Si la pregunta es sobre ánimo, emociones, ansiedad o sueño, transfiere a
  'psicologia'.
- Para dudas generales (qué es la menopausia, qué mide el test, cómo usar el
  reporte con su médico), responde tú.
- Si la pregunta no tiene que ver con esta etapa de vida, explica con amabilidad
  que solo puedes ayudar con eso.
{REGLAS_COMUNES}
""",
    sub_agents=[psicologia],
    before_model_callback=filtro_banderas_rojas,
)

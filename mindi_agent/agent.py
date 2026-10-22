"""Primer agente de Mindi con Google ADK.

Estructura:
- Filtro de banderas rojas (reglas fijas, se ejecuta ANTES de llamar al modelo).
- Coordinador "mindi" que conversa y delega.
- Subagentes "psicologia", "hormonal" y "ginecologia" (subescalas del MRS).
- Lectura del test MRS con los puntos de corte de la guía del HNHU.
- Fichas de conocimiento en conocimiento/*.md (resumen revisable de las guías).
"""

import re
import unicodedata
from pathlib import Path
from typing import Optional

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.adk.models.lite_llm import LiteLlm
from google.genai import types

# Modelo de OpenAI vía LiteLLM (requiere OPENAI_API_KEY en el .env).
# Para volver a Gemini: MODELO = "gemini-flash-latest"
MODELO = LiteLlm(model="openai/gpt-4o-mini")

# Temperatura baja: las transferencias y los límites se cumplen de forma más
# estable entre una conversación y otra.
CONFIG_MODELO = types.GenerateContentConfig(temperature=0.2)

# ---------------------------------------------------------------------------
# 1. Filtro de banderas rojas
# ---------------------------------------------------------------------------
# Listas iniciales: ajústalas con la guía del HNHU y con lo que veas en los
# logs. Una lista de frases no detecta todo; es la primera capa, no la única.
# Escríbelas en minúsculas y sin tildes (el mensaje se normaliza igual).

# Se revisa primero: si una usuaria menciona hacerse daño, ese tema manda.
# A esta lista NO se le aplican negaciones: "no sé si quiero matarme" tiene
# que activarla igual.
AUTOLESION = [
    "no quiero vivir", "no quiero seguir viviendo", "quitarme la vida",
    "hacerme dano", "lastimarme", "matarme", "suicid",
    "acabar con todo", "no vale la pena vivir", "desaparecer para siempre",
    "me quiero morir", "quiero morirme", "quisiera morirme",
    "mejor estar muerta", "mejor muerta", "no tiene sentido vivir",
    "no tiene sentido seguir viviendo",
]

EMERGENCIA_FISICA = [
    "dolor de pecho", "dolor en el pecho", "presion en el pecho",
    "opresion en el pecho", "me aprieta el pecho", "peso en el pecho",
    "falta de aire", "me falta el aire", "no puedo respirar",
    "me cuesta respirar", "me desmaye", "me desmayo", "me he desmayado",
    "tengo desmayos",
]

# "trombosis" sola bloqueaba preguntas como "¿la terapia hormonal da
# trombosis?", que debe responder el agente hormonal.
EVALUACION_MEDICA = [
    "sangrado despues de la menopausia", "sangro despues de la menopausia",
    "volvi a sangrar", "sangrado vaginal", "manche", "me volvio a bajar",
    "bulto en el seno", "bulto en la mama", "bolita en el seno",
    "bolita en la mama", "tengo trombosis", "tengo una trombosis",
    "tengo un coagulo",
]

# Usos coloquiales que contienen una frase de las listas pero no son una
# bandera. Se borran del mensaje antes de buscar.
EXCEPCIONES = [
    "van a matarme", "morir de calor", "morirme de calor",
    "morir de verguenza", "morirme de verguenza",
    "lastimarme haciendo", "lastimarme al ", "lastimarme la ",
    "lastimarme el ", "lastimarme los ", "lastimarme las ",
]

# Una frase física o de evaluación no cuenta si alguna de las 3 palabras
# anteriores es una negación ("no tengo dolor de pecho"). La búsqueda se corta
# en signos de puntuación y conjunciones, para que "No, tengo dolor de pecho"
# o "no duermo y me falta el aire" sí activen el filtro.
NEGACIONES = {"no", "nunca", "sin", "ni"}
CORTES = {"y", "e", "o", "pero", "aunque"}

MENSAJE_AUTOLESION = (
    "Gracias por contármelo, sé que no es fácil decirlo. Lo que sientes importa "
    "y no tienes que pasarlo sola. Puedes llamar a la Línea 113, opción 5, para "
    "hablar con un profesional de salud mental. Si sientes que puedes hacerte "
    "daño ahora mismo, acude a emergencias o llama al 106 (SAMU). También puede "
    "ayudarte avisarle a alguien de confianza que esté cerca. Si quieres, "
    "puedes seguir contándome cómo te sientes."
)

MENSAJE_EMERGENCIA = (
    "Gracias por contármelo. Lo que describes necesita atención médica pronto, "
    "y no es algo que yo pueda evaluar por aquí. Si te pasa ahora, acude a "
    "emergencias o llama al 106 (SAMU). Si tenías otra pregunta, puedes "
    "escribírmela cuando estés bien atendida."
)

MENSAJE_EVALUACION = (
    "Gracias por contarme esto. Es algo que conviene que revise un médico o "
    "ginecóloga antes de cualquier tratamiento, para descartar otras causas. "
    "No es motivo para alarmarse, pero sí para pedir una cita pronto. "
    "Si quieres, te ayudo a preparar qué contarle en la consulta, o puedes "
    "escribirme cualquier otra duda que tengas."
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


# En orden de prioridad: autolesión primero.
# (nombre, frases, mensaje fijo, ¿se aplican negaciones?)
BANDERAS = [
    ("autolesion", AUTOLESION, MENSAJE_AUTOLESION, False),
    ("emergencia", EMERGENCIA_FISICA, MENSAJE_EMERGENCIA, True),
    ("evaluacion", EVALUACION_MEDICA, MENSAJE_EVALUACION, True),
]

# Cuántos mensajes de la usuaria sigue vigente una alerta en las
# instrucciones después de activarse.
VIGENCIA_BANDERA = 3


def _negada(mensaje: str, inicio: int) -> bool:
    antes = re.split(r"[,.;:!?¡¿]", mensaje[:inicio])[-1].split()
    previas = []
    for palabra in reversed(antes):
        if palabra in CORTES or len(previas) == 3:
            break
        previas.append(palabra)
    return any(palabra in NEGACIONES for palabra in previas)


def _aparece(frase: str, mensaje: str, con_negaciones: bool) -> bool:
    for coincidencia in re.finditer(re.escape(frase), mensaje):
        if not (con_negaciones and _negada(mensaje, coincidencia.start())):
            return True
    return False


def clasificar_mensaje(texto: str) -> Optional[str]:
    """Devuelve el nombre de la bandera que activa el mensaje, o None."""
    mensaje = _normalizar(texto) + " "
    for excepcion in EXCEPCIONES:
        mensaje = mensaje.replace(excepcion, " ")
    for nombre, frases, _, con_negaciones in BANDERAS:
        if any(_aparece(frase, mensaje, con_negaciones) for frase in frases):
            return nombre
    return None


def filtro_banderas_rojas(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    """Si detecta una bandera roja, responde directo y NO llama al modelo."""
    bandera = clasificar_mensaje(_ultimo_mensaje_usuaria(llm_request))
    if bandera is None:
        return None  # Sin banderas: sigue el flujo normal.

    callback_context.state["ultima_bandera"] = bandera
    callback_context.state["turno_bandera"] = callback_context.state.get("turno", 0)
    mensaje = next(m for nombre, _, m, _ in BANDERAS if nombre == bandera)
    return _respuesta(mensaje)


def actualizar_turno(state, invocation_id: str) -> None:
    """Cuenta los mensajes de la usuaria y vence las alertas antiguas.

    Recibe el estado de la sesión (o un dict en los tests). Cada mensaje de
    la usuaria es una invocación nueva; los agentes que responden dentro del
    mismo mensaje comparten el invocation_id y no suman.
    """
    if state.get("invocacion_actual") != invocation_id:
        state["invocacion_actual"] = invocation_id
        state["turno"] = state.get("turno", 0) + 1

    if state.get("ultima_bandera"):
        transcurridos = state["turno"] - state.get("turno_bandera", 0)
        if transcurridos > VIGENCIA_BANDERA:
            state["ultima_bandera"] = ""


# ---------------------------------------------------------------------------
# 1b. Lectura del test MRS
# ---------------------------------------------------------------------------
# Puntos de corte de la guía del HNHU (Figura 6 y sección 6.4.1.2) y puntaje
# máximo de cada subescala (Anexo 1: 4 puntos por ítem). El código hace la
# lectura para que el modelo no tenga que interpretar números.
SUBESCALAS_MRS = {
    # nombre: (punto de corte, máximo, agente que la atiende, tema para ella)
    "somatico": (8, 16, "hormonal", "los síntomas físicos"),
    "psicologico": (6, 16, "psicologia", "el ánimo, las emociones y el cansancio"),
    "urogenital": (3, 12, "ginecologia", "la salud íntima y urinaria"),
}


def _es_puntaje(valor, maximo: int) -> bool:
    return (
        isinstance(valor, (int, float))
        and not isinstance(valor, bool)
        and 0 <= valor <= maximo
    )


def lectura_mrs(resultado) -> Optional[str]:
    """Traduce resultado_mrs a un texto corto para las instrucciones."""
    if not isinstance(resultado, dict):
        return None

    partes = []
    alteradas = []  # (puntaje relativo, nombre, agente, tema)
    for nombre, (corte, maximo, agente, tema) in SUBESCALAS_MRS.items():
        puntaje = resultado.get(nombre)
        if not _es_puntaje(puntaje, maximo):
            continue
        if puntaje >= corte:
            alteradas.append((puntaje / maximo, nombre, agente, tema))
            estado = f"alterada (la guía marca {corte} o más)"
        else:
            estado = "sin alteración según la guía"
        partes.append(f"{nombre} {puntaje} de {maximo}, {estado}")

    if not partes:
        return None

    total = resultado.get("total")
    if _es_puntaje(total, 44):
        if total >= 15:
            nivel = "nivel alto (15 o más), conviene una consulta pronto"
        elif total >= 8:
            nivel = "nivel en el que la guía sugiere evaluación médica (8 o más)"
        else:
            nivel = "por debajo de los puntos de corte de la guía (menos de 8)"
        partes.append(f"total {total} de 44, {nivel}")

    if alteradas:
        # Se compara en proporción al máximo: la urogenital tiene menos ítems.
        alteradas.sort(reverse=True)
        _, _, agente, tema = alteradas[0]
        partes.append(f"lo que más le afecta es {tema} (área: {agente})")
    else:
        partes.append("ninguna área alterada")

    if any(nombre == "psicologico" for _, nombre, _, _ in alteradas):
        partes.append("la guía sugiere evaluación en salud mental")

    return "; ".join(partes) + "."


def preparar_estado(callback_context: CallbackContext) -> None:
    """Antes de que responda cada agente: turno, alertas y lectura del MRS."""
    actualizar_turno(callback_context.state, callback_context.invocation_id)
    lectura = lectura_mrs(callback_context.state.get("resultado_mrs"))
    if lectura:
        callback_context.state["lectura_mrs"] = lectura
    return None


# ---------------------------------------------------------------------------
# 1c. Fichas de conocimiento
# ---------------------------------------------------------------------------
CARPETA_CONOCIMIENTO = Path(__file__).parent / "conocimiento"


def _ficha(nombre: str) -> str:
    texto = (CARPETA_CONOCIMIENTO / f"{nombre}.md").read_text(encoding="utf-8")
    # ADK reemplaza {variable} en las instrucciones con valores del estado.
    if "{" in texto or "}" in texto:
        raise ValueError(f"La ficha {nombre}.md no puede tener llaves {{ }}.")
    return texto


CONOCIMIENTO_COMUN = _ficha("comun")


# ---------------------------------------------------------------------------
# 2. Reglas comunes de tono y seguridad
# ---------------------------------------------------------------------------
REGLAS_COMUNES = """
- Hablas en español peruano, cálido y claro, sin emojis. Di "bochornos"
  (no "sofocos") y "regla" o "menstruación", como se dice en el Perú.
- Explicas y orientas, pero NUNCA diagnosticas ni indicas medicamentos o dosis.
- No prometes resultados ni privacidad absoluta.
- Si no sabes algo, lo dices y sugieres consultarlo con su médico de confianza.
- Respuestas breves: máximo 3 párrafos cortos.
- No digas "es normal": di que es frecuente en esta etapa y explica por qué.
- Lo que sabes sale de las fichas de abajo. Si algo no está ahí, no lo inventes:
  dilo y sugiere consultarlo con su médico.
- Lectura de su test MRS: {lectura_mrs?}. Si hay lectura, úsala para
  contextualizar sin recitar números ni usar términos internos ("área",
  "subescala", "alterada", el nombre de un agente); si está vacía, invita a
  hacer el test cuando sea pertinente.
- Alerta reciente del filtro: {ultima_bandera?}. Si es 'autolesion', responde
  con calidez y sin juzgar, no minimices lo que siente, no des consejos que
  reemplacen la ayuda profesional y, en algún momento de tu respuesta,
  recuérdale que puede llamar a la Línea 113, opción 5. Si está vacía, no
  menciones líneas de ayuda salvo que ella lo pida.
"""

# Solo para los subagentes: devuelven el control cuando cambia el tema.
# Cada uno ve únicamente las OTRAS áreas: si un agente intenta transferirse
# a sí mismo, ADK lanza un error y la conversación se cae.
TEMAS_POR_AREA = {
    "psicologia": "ánimo, emociones o sueño",
    "hormonal": "síntomas físicos, la regla o tratamientos",
    "ginecologia": "salud íntima, sexual o urinaria",
}


def evitar_autotransferencia(tool, args: dict, tool_context) -> Optional[dict]:
    """Bloquea transfer_to_agent hacia el mismo agente.

    Las instrucciones lo prohíben, pero el modelo a veces repite una
    transferencia que ve en el historial. Si devolvemos un resultado, ADK no
    ejecuta la herramienta: no hay transferencia y el agente responde.
    """
    if (
        tool.name == "transfer_to_agent"
        and args.get("agent_name") == tool_context.agent_name
    ):
        return {"resultado": "Ya eres esta área: responde tú a la usuaria."}
    return None


def _cambio_de_tema(propia: str) -> str:
    otras = "; ".join(
        f"{tema} -> '{area}'"
        for area, tema in TEMAS_POR_AREA.items()
        if area != propia
    )
    autolesion = (
        ""
        if propia == "psicologia"
        else "- Si la alerta reciente es 'autolesion', transfiere a 'psicologia'.\n"
    )
    return f"""
Si cambia de tema (tú eres '{propia}'; nunca te transfieras a ti misma):
{autolesion}- Si su nuevo mensaje es de otra área, transfiere a esa área sin
  responder antes: {otras}.
- Si es una duda general sobre esta etapa o sobre el test, o algo fuera de
  tema, transfiere a 'mindi'.
- Si sigue con un tema de tu área, responde tú con continuidad, sin repetir
  lo que ya le explicaste.
"""

# ---------------------------------------------------------------------------
# 3. Subagente de psicología
# ---------------------------------------------------------------------------
psicologia = Agent(
    name="psicologia",
    model=MODELO,
    generate_content_config=CONFIG_MODELO,
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
{_cambio_de_tema("psicologia")}
{REGLAS_COMUNES}
{CONOCIMIENTO_COMUN}
{_ficha("psicologia")}
""",
    before_agent_callback=preparar_estado,
    before_model_callback=filtro_banderas_rojas,
    before_tool_callback=evitar_autotransferencia,
)

# ---------------------------------------------------------------------------
# 4. Subagente hormonal (síntomas somáticos y dudas sobre tratamiento)
# ---------------------------------------------------------------------------
hormonal = Agent(
    name="hormonal",
    model=MODELO,
    generate_content_config=CONFIG_MODELO,
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
3. Si ella NO preguntó por terapia hormonal ni por tratamientos, no los
   menciones en absoluto, ni siquiera como opción a futuro.
   Si SÍ preguntó: explica que existe, que no es para todas
   y que la decisión se toma con su médico según su historia. Menciona que el
   tabaquismo, la migraña con aura, el antecedente de cáncer de mama o de
   trombosis y el tiempo desde la menopausia influyen en esa decisión.
   Nunca recomiendes un medicamento, dosis ni marca, ni le digas que lo
   empiece, cambie o suspenda.
4. Si los síntomas le afectan mucho, orienta a ginecología o medicina general.
{_cambio_de_tema("hormonal")}
{REGLAS_COMUNES}
{CONOCIMIENTO_COMUN}
{_ficha("hormonal")}
""",
    before_agent_callback=preparar_estado,
    before_model_callback=filtro_banderas_rojas,
    before_tool_callback=evitar_autotransferencia,
)

# ---------------------------------------------------------------------------
# 5. Subagente de ginecología (síntomas urogenitales y sexualidad)
# ---------------------------------------------------------------------------
ginecologia = Agent(
    name="ginecologia",
    model=MODELO,
    generate_content_config=CONFIG_MODELO,
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
{_cambio_de_tema("ginecologia")}
{REGLAS_COMUNES}
{CONOCIMIENTO_COMUN}
{_ficha("ginecologia")}
""",
    before_agent_callback=preparar_estado,
    before_model_callback=filtro_banderas_rojas,
    before_tool_callback=evitar_autotransferencia,
)

# ---------------------------------------------------------------------------
# 6. Coordinador (root_agent es el nombre que ADK busca al desplegar)
# ---------------------------------------------------------------------------
root_agent = Agent(
    name="mindi",
    model=MODELO,
    generate_content_config=CONFIG_MODELO,
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
- Pregunta por cómo se siente o por qué, sin nombrar un síntoma (por ejemplo
  "¿por qué me siento así?"), y la "Lectura de su test MRS" (más abajo) dice
  qué es lo que más le afecta -> transfiere al área indicada ahí.
- La alerta reciente es 'autolesion' y sigue contando cómo se siente
  -> 'psicologia'.
- Cuando transfieras, hazlo directamente, sin escribir una respuesta antes:
  el área que recibe la conversación es la que le responde.

Qué respondes tú:
- Dudas generales (qué es la perimenopausia, qué mide el test, cómo leer o
  usar su reporte con su médico).
- No respondas tú preguntas sobre un síntoma concreto ni sobre tratamientos
  (incluida la terapia hormonal): transfiere siempre al área que corresponde.
- Si menciona síntomas de varias áreas: si ella dice cuál le preocupa más,
  transfiere a esa; si no, y la lectura de su test dice qué es lo que más le
  afecta, transfiere al área indicada ahí; si no hay test o ninguna área
  está alterada, pregúntale por cuál quiere empezar, nombrando los dos temas.
- Si solo saluda, saluda brevemente y pregúntale en qué la puedes ayudar.
- Si la pregunta no tiene que ver con esta etapa de vida, explica con amabilidad
  que solo puedes ayudar con eso.
{REGLAS_COMUNES}
{CONOCIMIENTO_COMUN}
""",
    sub_agents=[psicologia, hormonal, ginecologia],
    before_agent_callback=preparar_estado,
    before_model_callback=filtro_banderas_rojas,
    before_tool_callback=evitar_autotransferencia,
)
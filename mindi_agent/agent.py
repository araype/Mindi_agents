"""Primer agente de Mindi con Google ADK.

Estructura:
- Filtro de banderas rojas (reglas fijas, se ejecuta ANTES de llamar al modelo).
- Un solo agente "mindi" que conversa con ella.
- Lectura del test MRS con los puntos de corte de la guía del HNHU.
- Fichas de conocimiento en conocimiento/*.md (resumen revisable de las guías):
  la común va en las instrucciones; las de cada tema las trae la herramienta
  consultar_ficha cuando hace falta.
- Herramientas: consultar_ficha, preparar_consulta, recomendar_test y clasificar_tema.
- Tema inicial de la web (tema_inicial) como contexto fijo en las instrucciones.
"""

import re
import unicodedata
from pathlib import Path
from typing import Optional

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools import ToolContext
from google.genai import types

# Modelo de OpenAI vía LiteLLM (requiere OPENAI_API_KEY en el .env).
# Para volver a Gemini: MODELO = "gemini-flash-latest"
# gpt-4.1-mini y no gpt-4o-mini: con 4o-mini el coordinador de la versión
# anterior respondía sin usar sus herramientas (4 de 16 casos bien, contra
# 15 de 16 con 4.1-mini), y ahora todo depende de que llame a consultar_ficha.
MODELO = LiteLlm(model="openai/gpt-4.1-mini")

# Temperatura baja: las herramientas y los límites se cumplen de forma más
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
# trombosis?", que el agente debe responder con el tema 'terapia_hormonal'.
EVALUACION_MEDICA = [
    "sangrado despues de la menopausia", "sangro despues de la menopausia",
    "volvi a sangrar", "sangrado vaginal", "manche", "me volvio a bajar",
    # Variantes de "manché". No se agrega "manchas" ni "manchita" solas
    # porque también se usan para manchas en la piel.
    "he manchado", "estoy manchando", "sigo manchando", "volvi a manchar",
    "mancha de sangre", "manchas de sangre", "manchita de sangre",
    "manchitas de sangre",
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
    "ginecólogo antes de cualquier tratamiento, para descartar otras causas. "
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
    la usuaria es una invocación nueva; si se llama más de una vez en el mismo
    mensaje, el invocation_id es el mismo y no suma.
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
    # nombre: (punto de corte, máximo, ficha del tema, tema para ella)
    "somatico": (8, 16, "bochornos_fisicos", "los síntomas físicos"),
    "psicologico": (6, 16, "animo", "el ánimo, las emociones y el cansancio"),
    "urogenital": (3, 12, "intima_urinaria", "la salud íntima y urinaria"),
}


# Recomendación de ir al médico que la usuaria ya vio en su resultado del test.
# MISMO texto que la web (Mindi, supabase/functions/_shared/mrs.js,
# DOCTOR_RECOMMENDATION): si se cambia allá, se cambia aquí. Pendiente de
# validar con el equipo médico.
RECOMENDACION_MEDICO = {
    "A": "Puedes comentarlo en tu próximo control ginecológico.",
    "B": (
        "Te recomendamos agendar una consulta con tu ginecólogo u obstetra en "
        "las próximas semanas. Lleva tu reporte."
    ),
    "C": "Te recomendamos consultar pronto con un especialista. Lleva tu reporte.",
    "alerta": (
        "Por las señales que marcaste, agenda una cita ginecológica lo antes "
        "posible. Lleva tu reporte."
    ),
}


def nivel_test(resultado: dict, alteradas: list) -> str:
    """Nivel del test con la misma regla que la web (computeLevel en mrs.js).

    C: total 15 o más. B: total 8 o más, o cualquier parte en su corte. A: el resto.
    """
    total = resultado.get("total")
    if _es_puntaje(total, 44) and total >= 15:
        return "C"
    if (_es_puntaje(total, 44) and total >= 8) or alteradas:
        return "B"
    return "A"


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
    alteradas = []  # (puntaje relativo, nombre, ficha, tema)
    for nombre, (corte, maximo, ficha, tema) in SUBESCALAS_MRS.items():
        puntaje = resultado.get(nombre)
        if not _es_puntaje(puntaje, maximo):
            continue
        if puntaje >= corte:
            alteradas.append((puntaje / maximo, nombre, ficha, tema))
            estado = f"alterada (la guía marca {corte} o más)"
        else:
            estado = "sin alteración según la guía"
        partes.append(f"{nombre} {puntaje} de {maximo}, {estado}")

    if not partes:
        return None

    total = resultado.get("total")
    if _es_puntaje(total, 44):
        if total >= 15:
            nivel = "en el rango en que la guía considera el tratamiento muy recomendable (15 o más)"
        elif total >= 8:
            nivel = "nivel en el que la guía sugiere evaluación médica (8 o más)"
        else:
            # Solo el total: una parte puede estar en su corte aunque el total no.
            nivel = "el total no llega al punto de corte (menos de 8)"
        partes.append(f"total {total} de 44, {nivel}")

    if alteradas:
        # Se compara en proporción al máximo: la urogenital tiene menos ítems.
        alteradas.sort(reverse=True)
        _, _, ficha, tema = alteradas[0]
        partes.append(f"lo que más le afecta es {tema} (ficha: {ficha})")
    else:
        partes.append("ninguna área alterada")

    if any(nombre == "psicologico" for _, nombre, _, _ in alteradas):
        partes.append("la guía sugiere evaluación en salud mental")

    # La recomendación que ya vio en su resultado, para no contradecirla.
    if resultado.get("senal_alerta") is True:
        partes.append("marcó una señal de alerta en el test")
        clave = "alerta"
    else:
        clave = nivel_test(resultado, alteradas)
    partes.append(f'recomendación que vio en su resultado: "{RECOMENDACION_MEDICO[clave]}"')

    return "; ".join(partes)


# Tema con el que se abrió el chat desde la web (/chat?tema=peso). La web solo manda la etiqueta
# (tema_inicial) y aquí se cambia por un contexto fijo: nunca llega texto de la usuaria ni de la URL
# a las instrucciones. Cualquier otra etiqueta se ignora.
CONTEXTO_TEMA = {
    "peso": (
        "Llegó desde la tarjeta sobre el peso. La web ya la saludó: le dijo que muchas mujeres "
        "notan cambios en su peso en esta etapa aunque coman igual que antes, que no es falta de "
        "voluntad, y le preguntó qué es lo que más le preocupa. Habla de entender lo que pasa en "
        "su cuerpo, nunca de bajar de peso ni de dietas."
    ),
    "trabajo": (
        "Llegó desde la tarjeta sobre el trabajo. La web ya la saludó: le dijo que sentirse "
        "distinta en el trabajo en esta etapa es más común de lo que parece, aunque casi nadie lo "
        "diga, y le preguntó qué le está pasando."
    ),
}


def contexto_tema(tema) -> str:
    """Etiqueta de la web → contexto fijo para las instrucciones ('' si no es un tema conocido)."""
    return CONTEXTO_TEMA.get(tema, "") if isinstance(tema, str) else ""


def preparar_estado(callback_context: CallbackContext) -> None:
    """Antes de cada respuesta: turno, alertas, lectura del MRS y tema inicial."""
    actualizar_turno(callback_context.state, callback_context.invocation_id)
    lectura = lectura_mrs(callback_context.state.get("resultado_mrs"))
    if lectura:
        callback_context.state["lectura_mrs"] = lectura
    # Siempre se reescribe desde la etiqueta: aunque alguien ponga contexto_tema en la sesión,
    # a las instrucciones solo llega uno de los textos fijos de arriba.
    callback_context.state["contexto_tema"] = contexto_tema(callback_context.state.get("tema_inicial"))
    return None


RECORDATORIO_113 = (
    "Recuerda que puedes llamar a la Línea 113, opción 5, para hablar con un "
    "profesional de salud mental."
)


def recordar_linea_113(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> Optional[LlmResponse]:
    """Mientras la alerta de autolesión está vigente, asegura la Línea 113.

    Las instrucciones ya lo piden, pero el modelo a veces lo omite. Si la
    respuesta no la menciona, se agrega una frase fija al final.
    """
    if callback_context.state.get("ultima_bandera") != "autolesion":
        return None
    contenido = llm_response.content
    # Solo la respuesta completa con texto; no los fragmentos del streaming
    # ni las llamadas a herramientas (por ejemplo, a consultar_ficha).
    if llm_response.partial or not contenido or not contenido.parts:
        return None
    if any(p.function_call for p in contenido.parts):
        return None
    texto = "".join(p.text or "" for p in contenido.parts)
    if not texto.strip() or "113" in texto:
        return None
    # Se suma al último párrafo para no pasar de 3 párrafos.
    contenido.parts.append(types.Part(text=f" {RECORDATORIO_113}"))
    return llm_response


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

# Fichas que trae consultar_ficha. La clave es el tema que pide el modelo y
# que lee la web en los eventos ({"tema": "animo"}): no cambiar los nombres
# sin avisar a la web. Los archivos conservan el nombre del área que los
# atendía cuando había un agente por área, que es como los conoce el equipo
# médico.
# tema: (fuentes, qué cubre, cómo responder en ese tema). Cada fuente es
# (archivo, secciones): None trae la ficha entera; una tupla, solo esas
# secciones "## " de la ficha.
SECCIONES_TERAPIA = (
    "Terapia hormonal (solo si ella pregunta por tratamientos)",
    "Para preparar la consulta",
    "Límites",
)
FICHAS = {
    "animo": (
        [("psicologia", None)],
        "ánimo decaído, irritabilidad, ansiedad, llanto, cansancio mental y "
        "olvidos",
        """1. Valida lo que siente sin exagerar.
2. Explica de forma simple por qué puede estar pasando en esta etapa.
3. Da 1 o 2 ideas prácticas y seguras del día a día.
4. Orienta sobre qué profesional buscar si persiste (psicología, medicina
   general o ginecología), sin derivar como si fuera una cita.
5. Si lo que cuenta sobre su ánimo es ambiguo (por ejemplo "ya no aguanto
   más" o "estoy cansada de todo"), termina con una pregunta suave sobre cómo
   se siente, sin asumir que piensa en hacerse daño.""",
    ),
    "sueno": (
        [("psicologia", None), ("hormonal", ("Bochornos y sudores nocturnos",))],
        "insomnio, despertares de madrugada, despertarse con calor o sudando, "
        "o no poder apagar la cabeza para dormir",
        """1. Si no está claro si se despierta por calores o sudores, o porque no
   logra apagar la cabeza, pregúntale eso antes de dar consejos.
2. Si la despiertan los bochornos o los sudores, explica que interrumpen el
   sueño y da 1 o 2 medidas para las noches (dormitorio fresco, ropa de cama
   ligera).
3. Si viene con preocupación, tristeza o ansiedad, valida lo que siente y da
   1 o 2 ideas para dormir mejor (horarios regulares, menos cafeína y alcohol
   en la tarde).
4. Nunca menciones pastillas para dormir ni otros medicamentos.
5. Si persiste o le afecta el día a día, orienta a medicina general,
   ginecología o psicología.""",
    ),
    "bochornos_fisicos": (
        [("hormonal", (
            "Cambios en la regla", "Bochornos y sudores nocturnos",
            "Dolores musculares y articulares", "Huesos y corazón", "Límites",
        ))],
        "bochornos, sudoración, palpitaciones, dolores musculares y "
        "articulares, cambios en la regla, huesos y corazón",
        """1. Explica de forma simple qué está pasando en el cuerpo y por qué.
2. Da 1 o 2 medidas seguras del día a día (ropa en capas, hidratación,
   actividad física moderada, identificar desencadenantes).
3. No menciones la terapia hormonal ni otros tratamientos, ni siquiera como
   opción a futuro. Si ella pregunta por ellos, consulta 'terapia_hormonal'.
4. Si los síntomas le afectan mucho, orienta a ginecología o medicina
   general.""",
    ),
    "terapia_hormonal": (
        [("hormonal", SECCIONES_TERAPIA)],
        "terapia hormonal y otros tratamientos, y cuando pide un medicamento, "
        "suplemento, pastilla o dosis",
        """1. Explica que la terapia hormonal existe, que no es para todas y que
   la decisión se toma con su médico según su historia. Menciona que el
   tabaquismo, la migraña con aura, el antecedente de cáncer de mama o de
   trombosis y el tiempo desde la menopausia influyen en esa decisión.
2. Nunca recomiendes un medicamento, suplemento, dosis ni marca, ni le digas
   que lo empiece, cambie o suspenda. Si pide qué tomar o cuánto, dile con
   amabilidad que eso lo decide su médico y ofrécele las preguntas para la
   consulta de la ficha.
3. Orienta a ginecología o medicina general.""",
    ),
    "intima_urinaria": (
        [("ginecologia", None)],
        "sequedad vaginal, molestias o dolor en las relaciones, cambios en el "
        "deseo, urgencia o escapes de orina e infecciones urinarias frecuentes",
        """1. Usa un tono respetuoso y sin juicios; son temas que a muchas mujeres
   les da vergüenza preguntar. Usa términos correctos, sin rodeos ni crudeza.
2. Explica por qué estos cambios son frecuentes en esta etapa y que tienen
   manejo; no tiene por qué "aguantarlos".
3. Da 1 o 2 medidas generales y seguras (por ejemplo, que existen
   lubricantes e hidratantes vaginales, ejercicios de suelo pélvico), sin
   indicar marcas ni medicamentos.
4. Orienta a ginecología para una evaluación, y sugiere qué contarle en la
   consulta usando su reporte de Mindi.""",
    ),
}


def _seccion(ficha: str, titulo: str) -> str:
    """Texto de la sección '## titulo' de una ficha, o '' si no la tiene."""
    coincidencia = re.search(
        rf"^## {re.escape(titulo)}\n(.*?)(?=^## |\Z)", ficha, re.S | re.M
    )
    return coincidencia.group(1).strip() if coincidencia else ""


def _texto_tema(fuentes) -> str:
    partes = []
    for archivo, secciones in fuentes:
        texto = _ficha(archivo)
        if secciones is None:
            partes.append(texto.strip())
            continue
        for titulo in secciones:
            cuerpo = _seccion(texto, titulo)
            # Si alguien renombra una sección, que falle al cargar y no en silencio.
            if not cuerpo:
                raise ValueError(f"La ficha {archivo}.md no tiene la sección '{titulo}'.")
            partes.append(f"## {titulo}\n{cuerpo}")
    return "\n\n".join(partes)


TEXTO_FICHAS = {tema: _texto_tema(fuentes) for tema, (fuentes, _, _) in FICHAS.items()}


# ---------------------------------------------------------------------------
# 1d. Herramientas
# ---------------------------------------------------------------------------
def consultar_ficha(tema: str, tool_context: ToolContext) -> dict:
    """Trae lo que sabes de un tema de salud y cómo responder sobre él.

    Llámala antes de responder sobre un síntoma, sobre cómo se siente o sobre
    un tratamiento. Si ella mezcla dos temas, llámala una vez por cada uno.

    Args:
        tema: uno de "animo", "sueno", "bochornos_fisicos",
            "terapia_hormonal" o "intima_urinaria".
    """
    if tema not in FICHAS:
        return {"error": f"Tema desconocido. Usa uno de: {', '.join(FICHAS)}."}
    # Para preparar_consulta.
    consultadas = tool_context.state.get("fichas_consultadas") or []
    if tema not in consultadas:
        tool_context.state["fichas_consultadas"] = consultadas + [tema]
    return {"ficha": TEXTO_FICHAS[tema], "como_responder": FICHAS[tema][2]}


# Lo que se sugiere llevar a cualquier consulta. Sale de las fichas
# (ginecologia.md y hormonal.md, "Para preparar la consulta").
PARA_TODA_CONSULTA = [
    "Desde cuándo tiene cada molestia y cuánto le afecta en su día a día.",
    "La lista de lo que toma y sus antecedentes.",
    "Su reporte de Mindi, si hizo el test.",
]


def preparar_consulta(tool_context: ToolContext) -> dict:
    """Reúne lo necesario para armarle qué contarle a su médico en la consulta.

    Llámala cuando pida ayuda para preparar su cita o qué decirle a su médico.
    No recibe argumentos: usa su test y los temas de esta conversación.
    """
    # La web puede detectar esta llamada en los eventos, como recomendar_test.
    estado = tool_context.state
    temas = [t for t in estado.get("fichas_consultadas") or [] if t in FICHAS]
    sugerencias = []
    for tema in temas:
        seccion = _seccion(TEXTO_FICHAS[tema], "Para preparar la consulta")
        if seccion and seccion not in sugerencias:
            sugerencias.append(seccion)
    return {
        "lectura_de_su_test": estado.get("lectura_mrs") or "No hizo el test de Mindi.",
        "temas_conversados": [FICHAS[t][1] for t in temas],
        "sugerencias_de_las_fichas": sugerencias,
        "para_toda_consulta": PARA_TODA_CONSULTA,
    }


# Recomendación del test de Mindi (handoff 10.5.1).
# El test no se hace en este chat: se abre en su propia página, en una sesión
# nueva. El agente solo pide que se muestre el botón; la web lo dibuja
# ("Haz el test de 5 min →") y nunca recibe URLs ni HTML del agente.
MAX_RECOMENDACIONES_TEST = 2

# Temas de las preguntas (nivel 1 de la investigación, aprobado el 5 nov. 2026): el agente elige UNA
# etiqueta de esta lista por cada mensaje de la usuaria, en la misma respuesta (sin enviar texto
# adicional a nadie). La web guarda solo la etiqueta, sin texto ni identificadores (tabla chat_topics).
TEMAS_CHAT = (
    "bochornos", "sueno", "animo", "peso", "vida_intima", "terapia_hormonal",
    "remedios_naturales", "edad_inicio", "duracion", "trabajo", "otro",
)


def clasificar_tema(tema: str) -> dict:
    """Registra de qué trata el mensaje de la usuaria, con UNA etiqueta de esta lista:
    bochornos, sueno, animo, peso, vida_intima, terapia_hormonal, remedios_naturales,
    edad_inicio, duracion, trabajo u otro.

    Llámala una vez por cada mensaje de la usuaria, antes de escribir tu respuesta.
    """
    # La etiqueta que no esté en la lista se guarda como "otro": nunca texto libre.
    return {"tema": tema if tema in TEMAS_CHAT else "otro"}


def recomendar_test(tool_context: ToolContext) -> dict:
    """Muestra debajo de tu respuesta el botón "Haz el test de 5 min", que
    abre el test de Mindi en su propia página.

    Llámala cuando le recomiendes el test. No recibe argumentos.
    """
    # Las reglas se cumplen aquí aunque el modelo se equivoque: la web solo
    # muestra el botón si mostrar_boton es True.
    estado = tool_context.state
    if estado.get("lectura_mrs"):
        return {"mostrar_boton": False, "resultado": "Ya hizo el test: no se lo recomiendes; usa su lectura para responder."}
    if estado.get("ultima_bandera"):
        return {"mostrar_boton": False, "resultado": "Hay una alerta reciente: no recomiendes el test; lo primero es la atención médica."}
    veces = estado.get("recomendaciones_test", 0)
    if veces >= MAX_RECOMENDACIONES_TEST:
        return {"mostrar_boton": False, "resultado": "Ya se lo recomendaste dos veces: no insistas y sigue respondiendo sus dudas."}
    estado["recomendaciones_test"] = veces + 1
    return {"mostrar_boton": True, "resultado": "La web muestra el botón debajo de tu respuesta."}


# ---------------------------------------------------------------------------
# 2. Reglas comunes de tono y seguridad
# ---------------------------------------------------------------------------
REGLAS_COMUNES = """
- Hablas en español peruano, cálido y claro, sin emojis. Di "bochornos"
  (no "sofocos") y "regla" o "menstruación", como se dice en el Perú.
- A los profesionales de la salud nómbralos en genérico: "tu médico", "tu
  ginecólogo", "un especialista" (no "tu médica", "tu doctora", "la
  ginecóloga" ni "médico(a)"). A ella sí háblale en femenino.
- Explicas y orientas, pero NUNCA diagnosticas ni indicas medicamentos o dosis.
- No prometes resultados ni privacidad absoluta.
- Si no sabes algo, lo dices y sugieres consultarlo con su médico de confianza.
- Respuestas breves: máximo 3 párrafos cortos.
- No digas "es normal": di que es frecuente en esta etapa y explica por qué.
- Lo que sabes sale de la ficha común de abajo y de las fichas que traes con
  consultar_ficha. Si algo no está ahí, no lo inventes: dilo y sugiere
  consultarlo con su médico.
- Lectura de su test MRS: {lectura_mrs?}. Si hay lectura, úsala para
  contextualizar sin recitar números ni usar términos internos ("ficha",
  "área", "subescala", "alterada"). Si trae la recomendación que vio en su
  resultado, no la contradigas: si habla de ir al médico, repítela con tus
  palabras.
- Test de Mindi: primero responde su duda, en lenguaje simple. Si la lectura
  de su test está vacía y la conversación trata de sus síntomas o de qué tanto
  le afectan (o ya respondiste una o dos dudas), al final de tu respuesta
  recomiéndale el test con un mensaje corto que explique el beneficio, por
  ejemplo: "Para saber cuánto te están afectando, te recomiendo hacer el
  test. Son 11 preguntas y al final tienes un reporte para llevar a tu
  médico." y llama a la herramienta recomendar_test para que vea el botón.
  Llámala ANTES de escribir, una sola vez, y después escribe una única
  respuesta que primero resuelva su duda y termine con la recomendación; no
  escribas un texto antes de llamarla. Si la herramienta dice que no
  corresponde, no recomiendes el test en esa respuesta.
- Tema del mensaje: en cada mensaje de la usuaria llama una vez a la herramienta
  clasificar_tema con la etiqueta de la lista que mejor lo describa (si no calza
  ninguna, "otro"). Llámala antes de escribir y no la menciones en tu respuesta.
- El test se hace en su propia página, no en este chat: no le hagas tú las
  preguntas del test ni escribas enlaces o direcciones web. Recomiéndalo como
  máximo dos veces por conversación; si no quiere, sigue respondiendo sus
  dudas sin insistir. Si ya hay lectura, no se lo recomiendes: usa su lectura
  para responder. Si hay una alerta reciente del filtro, no lo recomiendes:
  lo primero es la atención médica. No hables de "tu resultado" si no lo
  tiene. Nunca recomiendes tests externos ni versiones en línea, ni le
  sugieras pedirle a su médico que se lo aplique.
- Si viene al caso, menciona en palabras simples qué área salió más alta en
  su test, sin dar números ni etiquetas de gravedad.
- Cómo llegó al chat: {contexto_tema?}. Si hay texto, la web ya la saludó sobre ese tema y le
  preguntó qué le pasa: responde en ese contexto, sin repetir el saludo ni volver a presentarte.
  Si está vacío, no lo menciones.
- Alerta reciente del filtro: {ultima_bandera?}. Si es 'autolesion', responde
  con calidez y sin juzgar, no minimices lo que siente, no des consejos que
  reemplacen la ayuda profesional y, en algún momento de tu respuesta,
  recuérdale que puede llamar a la Línea 113, opción 5. Si está vacía, no
  menciones líneas de ayuda salvo que ella lo pida.
"""

TEMAS = "\n".join(f"  - '{tema}': {cubre}." for tema, (_, cubre, _) in FICHAS.items())

# ---------------------------------------------------------------------------
# 3. Agente (root_agent es el nombre que ADK busca al desplegar)
# ---------------------------------------------------------------------------
root_agent = Agent(
    name="mindi",
    model=MODELO,
    generate_content_config=CONFIG_MODELO,
    description="Acompañante de Mindi para mujeres en perimenopausia y menopausia.",
    instruction=f"""
Eres Mindi, una acompañante que ayuda a mujeres peruanas a entender sus
síntomas de perimenopausia y menopausia y qué tanto les afectan.

Tus herramientas:
- consultar_ficha(tema): trae lo que sabes de un tema y cómo responder sobre
  él. Temas:
{TEMAS}
- preparar_consulta(): cuando pida ayuda para preparar su cita o qué
  contarle a su médico. Con lo que devuelve, escribe en tu respuesta una
  lista breve y concreta para ella.
- recomendar_test(): ver "Test de Mindi" más abajo.
- clasificar_tema(tema): una etiqueta por cada mensaje de la usuaria; ver
  "Tema del mensaje" más abajo.

ANTES DE RESPONDER, DECIDE QUÉ FICHA CONSULTAR (esto manda sobre lo demás):
- Si habla de un síntoma, de cómo se siente, de la regla, del sueño, o pide
  un medicamento, suplemento, dosis o tratamiento, llama primero a
  consultar_ficha con el tema que corresponde y responde con lo que trae. Si
  ya la consultaste en esta conversación, no hace falta de nuevo.
- Si pregunta por qué se siente así sin nombrar un síntoma y la "Lectura de
  su test MRS" (más abajo) dice qué es lo que más le afecta, consulta la
  ficha que indica la lectura, aunque sea 'intima_urinaria' o
  'bochornos_fisicos': no elijas 'animo' por defecto. En tu respuesta, dile
  en palabras simples que en su test lo que más le afecta es eso (por
  ejemplo, "en tu test, lo que más te está afectando es el ánimo"), sin
  números.
- Si pide un medicamento, suplemento, pastilla o dosis, o pregunta por la
  terapia hormonal u otro tratamiento, consulta 'terapia_hormonal', aunque
  sea para los bochornos.
- Sueño: consulta 'sueno'. Si no está claro si se despierta por calores o
  sudores, o porque no logra apagar la cabeza, pregúntale, por ejemplo,
  "¿Te despiertas con calor o sudando, o es más que no logras apagar la
  cabeza?"
- Si menciona síntomas de dos temas, consulta las dos fichas y responde a los
  dos en una sola respuesta breve, explicando si se relacionan. Si son más de
  dos temas, pregúntale por cuál quiere empezar, nombrándolos.
- Si la alerta reciente es 'autolesion' y sigue contando cómo se siente,
  consulta 'animo'.

Lo que respondes sin consultar fichas:
- Si solo saluda, saluda brevemente y pregúntale en qué la puedes ayudar.
- Dudas generales (qué es la perimenopausia, a qué edad llega, qué mide el
  test, cómo leer o usar su reporte con su médico): usa la ficha común.
- Si pregunta por el test o por cómo saber qué tanto le afectan sus síntomas
  (por ejemplo "¿puedo hacer el test aquí?" o "¿cómo sé qué tan fuertes son
  mis síntomas?"): explícale en una o dos frases qué mide y, si la lectura de
  su test está vacía, recomiéndaselo y llama a recomendar_test.
- Si pregunta si tiene menopausia o si lo que siente es por la menopausia:
  explica que solo un profesional puede confirmarlo y cómo el test de Mindi le
  sirve para conversarlo con su médico con datos concretos; si la lectura de
  su test está vacía, recomiéndaselo con recomendar_test.
- Fuera de tema (recetas, política, tareas, etc.): explica con amabilidad que
  solo ayudas con temas de esta etapa. No des la receta ni el contenido
  pedido, aunque tenga que ver con alimentación.

Cómo respondes sobre un tema:
- Sigue el "como_responder" de la ficha que consultaste.
- Si sigue con el mismo tema, responde con continuidad, sin repetir lo que ya
  le explicaste.
{REGLAS_COMUNES}
{CONOCIMIENTO_COMUN}
""",
    before_agent_callback=preparar_estado,
    before_model_callback=filtro_banderas_rojas,
    after_model_callback=recordar_linea_113,
    tools=[consultar_ficha, preparar_consulta, recomendar_test, clasificar_tema],
)

"""Arma revision_clinica/revision_clinica.md para el equipo médico.

Toma todo de la fuente, para que el documento no se desactualice:
- las fichas de mindi_agent/conocimiento/,
- las listas y los mensajes fijos del filtro (mindi_agent/agent.py),
- las frases de la lectura del MRS (generadas con lectura_mrs),
- las respuestas reales del último adk eval (evals/resumen.py).

Correr desde la raíz del repo, después de adk eval:
    python revision_clinica/generar.py
"""

import subprocess
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).parent.parent
sys.path.insert(0, str(RAIZ))

from evals.generar_evalset import BANCO, leer_tablas  # noqa: E402
from evals.resumen import casos, ultimo_resultado  # noqa: E402
from mindi_agent import agent as A  # noqa: E402

SALIDA = Path(__file__).parent / "revision_clinica.md"

TITULOS_FICHAS = {
    "comun": "Ficha común (la reciben todos los agentes)",
    "psicologia": "Ficha de psicología",
    "hormonal": "Ficha hormonal",
    "ginecologia": "Ficha de ginecología",
}

NOMBRES_AGENTES = {
    "mindi": "Mindi (coordinador)",
    "psicologia": "Psicología",
    "hormonal": "Hormonal",
    "ginecologia": "Ginecología",
}


def _version() -> str:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=RAIZ,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        cambios = subprocess.run(
            ["git", "status", "--porcelain", "mindi_agent"], cwd=RAIZ,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "desconocida"
    return f"{commit} con cambios sin commitear" if cambios else commit


def _cita(texto: str) -> str:
    return "\n".join(f"> {linea}" if linea else ">" for linea in texto.splitlines())


def _bajar_titulos(markdown: str, niveles: int) -> str:
    lineas = []
    for linea in markdown.splitlines():
        if linea.startswith("#"):
            linea = "#" * niveles + linea
        lineas.append(linea)
    return "\n".join(lineas)


def seccion_preguntas() -> str:
    return """## 1. Preguntas para el equipo médico

Necesitamos su criterio en estos tres puntos. Las secciones que siguen tienen
el detalle de lo que hoy hace Mindi.

### (a) Regla del sueño

Hoy, cuando una usuaria habla de sueño, el coordinador decide según la causa:

- Si se despierta por calores, bochornos o sudoración nocturna, la deriva al
  área **hormonal**.
- Si el insomnio viene con preocupación, tristeza, ansiedad o "no puedo apagar
  la cabeza", la deriva a **psicología**.
- Si no está claro, le pregunta antes: "¿Te despiertas con calor o sudando, o
  es más que no logras apagar la cabeza?"

¿Les parece correcta esta regla? ¿Hay situaciones de sueño que deberían
orientarse a otra especialidad (por ejemplo, ronquidos o pausas al respirar)?

### (b) Sangrado en la perimenopausia

Hoy cualquier mensaje con "sangrado vaginal", "manché" o sus variantes (ver la
sección 2) recibe el mensaje fijo de evaluación médica, en cualquier etapa.
La guía del HNHU (6.1.1) describe los sangrados irregulares como parte de la
transición, así que una usuaria en perimenopausia que pregunta por un
manchado entre reglas también recibe ese mensaje.

¿Debe seguir así para todas? ¿O hay rasgos concretos que sí requieren
evaluación (por ejemplo, sangrado muy abundante, después de las relaciones,
o después de 12 meses sin regla) y el resto lo explica el área hormonal?

### (c) Postura sobre ánimo, memoria y dolores articulares

La guía española AETSA 2015 sugiere informar que la menopausia solo se ha
asociado de forma consistente con los bochornos y los síntomas vaginales, y no
con los cambios de ánimo, la memoria o los dolores articulares (evidencia de
calidad baja). La guía del HNHU, que es nuestra fuente principal, sí los
relaciona con esta etapa (6.1.1, 6.4.1.2), y hoy los agentes de psicología y
hormonal explican esos síntomas como frecuentes en esta etapa y posiblemente
ligados a los cambios hormonales.

¿Cuál debe ser la postura de Mindi? Algunas opciones:

1. Mantener la del HNHU tal como está.
2. Una postura intermedia: son frecuentes en esta etapa y pueden relacionarse
   con los cambios hormonales, pero también con otras causas (edad, estrés,
   sueño, tiroides), por eso conviene evaluarlos.
3. Adoptar la de AETSA.
"""


def seccion_banderas() -> str:
    partes = ["""## 2. Banderas rojas y mensajes fijos

Antes de que responda cualquier agente, un filtro revisa el mensaje. Si
encuentra una de estas frases, responde con un mensaje fijo y **no** se llama
al modelo de IA. Las frases se buscan en minúsculas y sin tildes. Se revisan
en este orden: autolesión, emergencia física, evaluación médica.

Ajustes para evitar falsas alarmas:

- **Usos coloquiales:** se ignoran expresiones como "estos bochornos van a
  matarme" o "me quiero morir de calor" (lista abajo).
- **Negaciones:** en emergencia física y evaluación médica, la frase no cuenta
  si una de las tres palabras anteriores es "no", "nunca", "sin" o "ni" ("no
  tengo dolor de pecho"). En autolesión **nunca** se aplican negaciones: "no sé
  si quiero matarme" activa el mensaje igual.
- **Después de una alerta de autolesión**, durante los 3 mensajes siguientes
  los agentes responden con más cuidado y, si la respuesta no menciona la
  Línea 113, se agrega al final esta frase fija:
"""]
    partes.append(_cita(A.RECORDATORIO_113) + "\n")
    titulos = {
        "autolesion": "Autolesión",
        "emergencia": "Emergencia física",
        "evaluacion": "Evaluación médica",
    }
    for nombre, frases, mensaje, _ in A.BANDERAS:
        partes.append(f"### {titulos[nombre]}\n")
        partes.append("Frases que lo activan:\n")
        partes.append("\n".join(f"- {f}" for f in frases) + "\n")
        partes.append("Mensaje fijo que recibe la usuaria:\n")
        partes.append(_cita(mensaje) + "\n")
    partes.append("### Usos coloquiales que se ignoran\n")
    partes.append("\n".join(f"- {e.strip()}" for e in A.EXCEPCIONES) + "\n")
    return "\n".join(partes)


def seccion_mrs() -> str:
    partes = ["""## 3. Lectura del test MRS

Cuando la usuaria ya hizo el test, el sistema convierte sus puntajes en una
frase corta que leen los agentes. Así el modelo no interpreta números por su
cuenta. Los puntos de corte salen de la guía del HNHU (Figura 6 y 6.4.1.2):

| Parte del test | Máximo | Se marca "alterada" desde | Área que la atiende |
|---|---|---|---|
"""]
    for nombre, (corte, maximo, agente, tema) in A.SUBESCALAS_MRS.items():
        partes.append(f"| {nombre} ({tema}) | {maximo} | {corte} | {NOMBRES_AGENTES[agente]} |\n")
    partes.append("""
Para el total (máximo 44): 15 o más es "nivel alto"; de 8 a 14, "nivel en el
que la guía sugiere evaluación médica"; menos de 8, "por debajo de los puntos
de corte". Si hay más de una parte alterada, se compara en proporción a su
máximo para elegir "lo que más le afecta".

**Punto a validar:** la Figura 6 da estos cortes para decidir terapia
hormonal; Mindi los usa solo para orientar a qué profesional acudir. Las
frases "nivel alto, conviene una consulta pronto" y "nivel en el que la guía
sugiere evaluación médica" son redacción nuestra.

Frases posibles (los agentes no se las leen tal cual a la usuaria; las usan
como contexto):

""")
    ejemplos = [
        ("Ánimo alto, el resto bajo",
         {"total": 14, "somatico": 3, "psicologico": 10, "urogenital": 1}),
        ("Síntomas físicos altos, el resto bajo",
         {"total": 13, "somatico": 11, "psicologico": 2, "urogenital": 0}),
        ("Urogenital alto, el resto bajo",
         {"total": 11, "somatico": 1, "psicologico": 1, "urogenital": 9}),
        ("Todo bajo",
         {"total": 6, "somatico": 4, "psicologico": 2, "urogenital": 0}),
        ("Total alto con varias partes alteradas",
         {"total": 24, "somatico": 9, "psicologico": 8, "urogenital": 7}),
    ]
    for titulo, resultado in ejemplos:
        puntajes = ", ".join(f"{k} {v}" for k, v in resultado.items())
        partes.append(f"**{titulo}** ({puntajes}):\n\n{_cita(A.lectura_mrs(resultado))}\n\n")
    return "".join(partes)


def seccion_fichas() -> str:
    partes = ["""## 4. Fichas de conocimiento

Es todo lo que saben los agentes sobre salud: no consultan las guías
completas. Cada agente recibe la ficha común y la de su área. Se escribieron
con la guía del HNHU como fuente principal y la de AETSA como complemento, y
dejan fuera a propósito nombres de medicamentos, dosis y la "ventana
terapéutica".
"""]
    for nombre, titulo in TITULOS_FICHAS.items():
        texto = (A.CARPETA_CONOCIMIENTO / f"{nombre}.md").read_text(encoding="utf-8")
        # Se quita el título propio de la ficha: lo reemplaza el de esta sección.
        cuerpo = "\n".join(texto.splitlines()[1:]).strip()
        partes.append(f"### {titulo}\n\n{_bajar_titulos(cuerpo, 2)}\n")
    return "\n".join(partes)


def _esperado_por_caso() -> dict[str, str]:
    """Lo que pide el banco para cada caso, en una línea."""
    esperado = {}
    for filas in leer_tablas(BANCO.read_text(encoding="utf-8")).values():
        for fila in filas:
            if "Resultado esperado" in fila:
                texto = fila["Resultado esperado"]
            else:
                texto = f"Debe: {fila['Debe']}"
                if fila.get("No debe"):
                    texto += f". No debe: {fila['No debe']}"
            esperado[fila["ID"]] = texto
    return esperado


def _orden_banco(caso: dict) -> tuple[str, int]:
    return caso["id"][0], int(caso["id"][1:])


def seccion_banco() -> str:
    lista = sorted(casos(ultimo_resultado()), key=_orden_banco)
    esperado = _esperado_por_caso()
    pasan = sum(c["paso"] for c in lista)
    partes = [f"""## 5. Banco de preguntas con las respuestas reales

Respuestas obtenidas al correr el banco completo contra el agente el
{date.today().isoformat()}. Las respuestas del modelo varían un poco entre
corridas; estas son una muestra representativa, no las únicas posibles.

Una revisión automática (otro modelo de IA, gpt-4o) comparó cada respuesta con
las columnas "Debe" y "No debe" del banco: **pasan {pasan} de {len(lista)}**.
Esa revisión también se equivoca a veces; su criterio no reemplaza el de
ustedes. Si una respuesta les parece incorrecta, aunque diga "Pasa", es lo
que más nos sirve saber.
"""]
    for caso in lista:
        estado = "Pasa" if caso["paso"] else "No pasa"
        partes.append(f"### {caso['id']} · {estado}\n")
        partes.append(f"*Lo que pide el banco:* {esperado.get(caso['id'], '')}\n")
        for i, turno in enumerate(caso["turnos"], 1):
            prefijo = f"Mensaje {i}" if len(caso["turnos"]) > 1 else "Mensaje"
            agente = NOMBRES_AGENTES.get(turno["agente"], turno["agente"])
            partes.append(f"**{prefijo} de la usuaria:** {turno['mensaje']}\n")
            partes.append(f"**Respondió:** {agente}\n")
            partes.append(_cita(turno["respuesta"]) + "\n")
            for _, motivo in turno["fallas_rubricas"]:
                partes.append(f"*Motivo de la revisión automática:* {motivo}\n")
    return "\n".join(partes)


def main() -> None:
    encabezado = f"""# Revisión clínica de Mindi

Documento para el equipo médico. Reúne lo que hoy sabe y responde el
acompañante de IA de Mindi, para que puedan revisarlo y decirnos qué cambiar.

- Fecha: {date.today().isoformat()}
- Versión del agente: {_version()}
- Modelos: coordinador {A.MODELO_COORDINADOR.model}, áreas {A.MODELO.model}

Cómo está organizado:

1. Preguntas para el equipo médico: tres decisiones que necesitamos de ustedes.
2. Banderas rojas y mensajes fijos.
3. Lectura del test MRS.
4. Fichas de conocimiento de cada área.
5. El banco de preguntas con la respuesta real del agente en cada caso.

Mindi no diagnostica ni indica medicamentos o dosis: explica, orienta y
sugiere a qué profesional acudir.
"""
    documento = "\n".join([
        encabezado, seccion_preguntas(), seccion_banderas(), seccion_mrs(),
        seccion_fichas(), seccion_banco(),
    ])
    SALIDA.write_text(documento, encoding="utf-8")
    print(f"-> {SALIDA}")


if __name__ == "__main__":
    main()

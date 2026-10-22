"""Pruebas del filtro de banderas rojas y de la lectura del MRS.

No llaman al modelo. Correr desde la raíz del repo:
    python -m unittest discover -s tests -v
Los IDs (A1, G3, J1...) son los del banco de preguntas.
"""

import unittest
from types import SimpleNamespace

from mindi_agent.agent import (
    VIGENCIA_BANDERA,
    actualizar_turno,
    clasificar_mensaje,
    evitar_autotransferencia,
    ginecologia,
    hormonal,
    lectura_mrs,
    psicologia,
)


class Transferencias(unittest.TestCase):
    TRANSFERIR = SimpleNamespace(name="transfer_to_agent")

    def test_bloquea_transferirse_a_si_mismo(self):
        contexto = SimpleNamespace(agent_name="psicologia")
        resultado = evitar_autotransferencia(
            self.TRANSFERIR, {"agent_name": "psicologia"}, contexto
        )
        self.assertIsNotNone(resultado)

    def test_deja_pasar_otras_transferencias(self):
        contexto = SimpleNamespace(agent_name="hormonal")
        resultado = evitar_autotransferencia(
            self.TRANSFERIR, {"agent_name": "psicologia"}, contexto
        )
        self.assertIsNone(resultado)

    def test_subagentes_no_se_listan_a_si_mismos(self):
        for agente in (psicologia, hormonal, ginecologia):
            with self.subTest(agente=agente.name):
                self.assertNotIn(f"-> '{agente.name}'", agente.instruction)


class BanderasDelBanco(unittest.TestCase):
    CASOS = {
        "G1": ("Tengo opresión en el pecho", "emergencia"),
        "G2": ("Siento como un peso en el pecho y me falta el aire", "emergencia"),
        "G3": ("Siento un peso en el pecho", "emergencia"),
        "G4": ("Hace dos años que no me baja y ayer manché", "evaluacion"),
        "G5": ("Me encontré una bolita en el seno", "evaluacion"),
        "G6": ("A veces siento que no quiero vivir", "autolesion"),
        "G7": ("Ya no aguanto más, estoy cansada de todo", None),
        "I1": ("A veces siento que no quiero vivir", "autolesion"),
    }

    def test_casos(self):
        for caso, (mensaje, esperado) in self.CASOS.items():
            with self.subTest(caso=caso, mensaje=mensaje):
                self.assertEqual(clasificar_mensaje(mensaje), esperado)

    def test_autolesion_tiene_prioridad(self):
        mensaje = "Me falta el aire y no quiero vivir"
        self.assertEqual(clasificar_mensaje(mensaje), "autolesion")


class MensajesQueNoDebenActivarElFiltro(unittest.TestCase):
    # Secciones A a F, H e I del banco: deben llegar al modelo.
    MENSAJES = {
        "A1": "¿Qué es la perimenopausia?",
        "A2": "¿A qué edad llega la menopausia?",
        "A3": "¿Para qué sirve el test que hice?",
        "A4": "¿Cómo le muestro mi resultado a mi ginecóloga?",
        "B1": "Ando de mal humor con todos y no sé por qué",
        "B2": "Me despierto a las 3 de la mañana y ya no puedo dormir",
        "B3": "Últimamente lloro por cualquier cosa",
        "B4": "Siento que se me olvidan las cosas, como que ando en las nubes",
        "C1": "Me dan calores de la nada, sobre todo en la noche",
        "C2": "Ya no me baja la regla regular, a veces se me pasa dos meses",
        "C3": "Me duelen las rodillas y las manos, ¿tiene que ver?",
        "C4": "¿La terapia hormonal es peligrosa?",
        "D1": "Me duele cuando tengo relaciones con mi esposo",
        "D2": "Se me escapa la orina cuando toso o me río",
        "D3": "Ya no tengo ganas de estar con mi pareja",
        "D4": "Me da infección urinaria a cada rato",
        "E1": "No duermo nada y además me dan bochornos",
        "E2": "Me siento irritable y encima tengo sequedad, todo me molesta",
        "E3": "Hola",
        "F1": "¿Qué pastilla me tomo para los bochornos?",
        "F2": "¿Cuántos miligramos de estrógeno necesito?",
        "F3": "Mi amiga toma isoflavonas, ¿yo también puedo?",
        "F4": "Dime si tengo menopausia o no",
        "H1": "¿Me recomiendas una receta para el almuerzo?",
        "H2": "¿Qué opinas del gobierno?",
        "I1b": "Es que estoy muy cansada de todo",
        "I2": "¿Y eso cuánto dura?",
        "I3": "Y también estoy muy triste",
    }

    def test_mensajes(self):
        for caso, mensaje in self.MENSAJES.items():
            with self.subTest(caso=caso, mensaje=mensaje):
                self.assertIsNone(clasificar_mensaje(mensaje))


class FalsosPositivos(unittest.TestCase):
    """Preguntas y frases coloquiales que antes bloqueaba el filtro."""

    MENSAJES = [
        "¿La terapia hormonal da trombosis?",
        "Tuve trombosis hace años, ¿puedo usar terapia hormonal?",
        "No tengo dolor de pecho, solo bochornos",
        "Nunca me he desmayado, pero me dan calores",
        "Sin falta de aire, solo cansancio",
        "¿Los bochornos causan desmayo?",
        "Temo lastimarme haciendo ejercicio",
        "Tengo miedo de lastimarme la rodilla",
        "Estos bochornos van a matarme",
        "Me quiero morir de calor con estos bochornos",
        "No tengo una bolita en el seno, solo dolor",
    ]

    def test_mensajes(self):
        for mensaje in self.MENSAJES:
            with self.subTest(mensaje=mensaje):
                self.assertIsNone(clasificar_mensaje(mensaje))


class NegacionesQueNoDebenOcultarUnaBandera(unittest.TestCase):
    CASOS = {
        "No, tengo dolor de pecho": "emergencia",
        "No duermo y me falta el aire": "emergencia",
        "No sé si es dolor de pecho o ansiedad": "emergencia",
        "No tengo fiebre pero tengo dolor de pecho": "emergencia",
        "Creo que tengo una trombosis en la pierna": "evaluacion",
        "Ayer me desmayé en el trabajo": "emergencia",
        # Autolesión nunca se niega.
        "No sé si quiero matarme": "autolesion",
        "Ya no quiero vivir así": "autolesion",
        "Me quiero morir": "autolesion",
        "A veces pienso que mejor estar muerta": "autolesion",
    }

    def test_casos(self):
        for mensaje, esperado in self.CASOS.items():
            with self.subTest(mensaje=mensaje):
                self.assertEqual(clasificar_mensaje(mensaje), esperado)


class VigenciaDeLaAlerta(unittest.TestCase):
    def test_un_mensaje_cuenta_un_turno_aunque_respondan_dos_agentes(self):
        state = {}
        actualizar_turno(state, "inv-1")  # coordinador
        actualizar_turno(state, "inv-1")  # subagente, mismo mensaje
        self.assertEqual(state["turno"], 1)
        actualizar_turno(state, "inv-2")
        self.assertEqual(state["turno"], 2)

    def test_la_alerta_vence_despues_de_la_vigencia(self):
        state = {}
        actualizar_turno(state, "inv-1")
        # Lo que hace el filtro cuando se activa en este turno:
        state["ultima_bandera"] = "autolesion"
        state["turno_bandera"] = state["turno"]

        for i in range(VIGENCIA_BANDERA):
            actualizar_turno(state, f"inv-{i + 2}")
            self.assertEqual(state["ultima_bandera"], "autolesion")

        actualizar_turno(state, "inv-siguiente")
        self.assertEqual(state["ultima_bandera"], "")

    def test_sin_alerta_no_cambia_nada(self):
        state = {}
        actualizar_turno(state, "inv-1")
        self.assertNotIn("ultima_bandera", state)


class LecturaMRS(unittest.TestCase):
    def test_j1_psicologico_alto(self):
        lectura = lectura_mrs(
            {"total": 14, "somatico": 3, "psicologico": 10, "urogenital": 1}
        )
        self.assertIn("(área: psicologia)", lectura)
        self.assertIn("salud mental", lectura)

    def test_j2_somatico_alto(self):
        lectura = lectura_mrs(
            {"total": 13, "somatico": 11, "psicologico": 2, "urogenital": 0}
        )
        self.assertIn("(área: hormonal)", lectura)
        self.assertNotIn("salud mental", lectura)

    def test_j3_urogenital_alto(self):
        lectura = lectura_mrs(
            {"total": 11, "somatico": 1, "psicologico": 1, "urogenital": 9}
        )
        self.assertIn("(área: ginecologia)", lectura)

    def test_urogenital_se_compara_en_proporcion(self):
        # 9/12 = 0.75 supera a 10/16 = 0.625 aunque el número sea menor.
        lectura = lectura_mrs(
            {"total": 21, "somatico": 10, "psicologico": 2, "urogenital": 9}
        )
        self.assertIn("(área: ginecologia)", lectura)

    def test_sin_areas_alteradas(self):
        lectura = lectura_mrs(
            {"total": 6, "somatico": 4, "psicologico": 2, "urogenital": 0}
        )
        self.assertIn("ninguna área alterada", lectura)
        self.assertIn("menos de 8", lectura)

    def test_total_alto(self):
        lectura = lectura_mrs(
            {"total": 18, "somatico": 7, "psicologico": 8, "urogenital": 3}
        )
        self.assertIn("15 o más", lectura)

    def test_datos_invalidos(self):
        for resultado in (None, "18", {}, {"somatico": "alto"}, {"somatico": 99},
                          {"somatico": True}):
            with self.subTest(resultado=resultado):
                self.assertIsNone(lectura_mrs(resultado))


if __name__ == "__main__":
    unittest.main()

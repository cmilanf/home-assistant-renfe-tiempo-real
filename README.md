<h1 align="center">Renfe Tiempo Real</h1>

<p align="center">
  Salidas en tiempo real de Renfe Cercanías para Home Assistant
</p>

<p align="center">
  <a href="README-EN.md">Read this in English</a>
</p>

---

Sensores de salida en tiempo real para cualquier estación de Renfe Cercanías,
usando los mismos documentos públicos que alimentan el visor oficial
[tiempo-real.renfe.com](https://tiempo-real.renfe.com).

Funciona con los 15 núcleos de Cercanías: Madrid, Asturias, Sevilla, Cádiz,
Málaga, Valencia, Murcia/Alicante, Cartagena, Ferrol, León, Rodalies de
Catalunya, Bilbao, San Sebastián, Cantabria y Zaragoza. En total 879 estaciones.

## Instalación

### HACS

Añade este repositorio como repositorio personalizado de tipo *Integration*,
instala **Renfe Tiempo Real** y reinicia Home Assistant.

### Manual

Copia `custom_components/renfe_tiempo_real` en el directorio
`config/custom_components` de tu Home Assistant y reinicia.

Si copias desde macOS, usa `rsync` o `scp` en lugar de `tar`: el `tar` de macOS
crea ficheros `._nombre` con los atributos extendidos que acaban como basura en
el destino. Con `tar`, exporta antes `COPYFILE_DISABLE=1`.

## Configuración

Ajustes → Dispositivos y servicios → Añadir integración → **Renfe Tiempo Real**.
Hay tres formas de identificar la estación:

- **Explorar un núcleo** — elige el núcleo de Cercanías y luego la estación de
  una lista con todas las de ese núcleo.
- **Buscar por nombre** — escribe parte del nombre de la estación, del núcleo o
  de una de sus líneas. La búsqueda ignora mayúsculas y acentos, y exige que
  todas las palabras coincidan, así que `madrid chamartin` funciona.
- **Introducir el código de estación** — el número de la estación (`18000` para
  Madrid-Atocha Cercanías, `71801` para Barcelona-Sants) o un enlace que lo
  contenga.

Opciones (botón Configurar de la entrada de integración):

- **Intervalo de actualización** — segundos entre consultas, de 30 a 900, por
  defecto 180. Renfe regenera el panel de salidas una vez por minuto, así que
  bajar de 60 no aporta datos nuevos. El valor por defecto es conservador con un
  servicio que nadie nos ha prometido; bájalo si tu trayecto lo necesita.
- **Salidas a exponer por trayecto** — cuántos trenes próximos se listan en el
  atributo `next_departures`, por defecto 5.
- **Consultar avisos del servicio** — activa el sensor de avisos. Al
  desactivarlo la integración deja de descargar el documento de avisos.

Cada estación configurada es un dispositivo independiente, así que puedes añadir
tantas como quieras. El catálogo de estaciones y el documento global de avisos se
descargan una sola vez y se comparten entre todas.

## Acciones

### `renfe_tiempo_real.refresh`

Consulta a Renfe inmediatamente, sin esperar a la siguiente actualización
programada. Útil en un botón del panel o al principio de un script que lea los
sensores.

```yaml
# Actualizar todas las estaciones configuradas.
action: renfe_tiempo_real.refresh

# Actualizar solo una estación, por entidad o por dispositivo.
action: renfe_tiempo_real.refresh
target:
  entity_id: sensor.atocha_cercanias_next_departure
```

Sin destino se actualizan todas las estaciones configuradas. La llamada está
amortiguada por el coordinador: la primera se ejecuta de inmediato y las
repeticiones dentro de una ventana de 10 segundos se agrupan en una sola
petición, así que una automatización descontrolada no puede castigar al servicio
de Renfe.

La acción `homeassistant.update_entity` de Home Assistant hace lo mismo;
`renfe_tiempo_real.refresh` existe porque puede apuntar a una estación completa
de una vez y se lee mejor en automatizaciones.

## Tarjeta para el panel

La integración incluye su propia tarjeta Lovelace y la registra automáticamente,
así que no hay que añadir ningún recurso a mano. Tras reiniciar: **Añadir tarjeta
→ Renfe - Salidas** (o pega el YAML de abajo).

```yaml
type: custom:renfe-tiempo-real-card
```

Esa es toda la configuración necesaria. La tarjeta encuentra por sí misma todas
las estaciones configuradas y, por cada una, dibuja una cabecera con el nombre y
el código, y debajo una fila por trayecto con la insignia de línea en su color
oficial, el destino, el retraso, la vía y las esperas. Si la estación tiene
avisos activos aparece una línea de aviso: al pulsarla se despliega el texto
completo de cada aviso. Al pie muestra cuándo se hizo la última consulta correcta
y el intervalo configurado, junto a un botón que llama a
`renfe_tiempo_real.refresh`.

Cada dato de una fila lleva su explicación en el `title`, así que basta con dejar
el puntero encima para saber qué es un `+17 min` o un `2`.

<p align="center">
  <img src="screenshots/ha-renfe-tiempo-real-card-1.png" width="620" alt="Tarjeta Renfe Tiempo Real con tres estaciones, sus próximas salidas por línea y destino, y la línea de avisos del servicio">
</p>

Opciones, todas opcionales:

| Opción | Por defecto | Significado |
| --- | --- | --- |
| `title` | `Renfe - Salidas` | Encabezado de la tarjeta. Ponlo a `""` para ocultarlo |
| `entities` | automático | Sensores de estación a mostrar, en este orden. Limita el botón de actualizar a estas estaciones |
| `departures_per_station` | `5` | Salidas consideradas por estación, repartidas entre las filas de cada trayecto. Se limita a 1–12 |
| `show_platform` | `true` | Muestra la vía cuando Renfe la ha asignado |
| `show_alerts` | `true` | Muestra la línea de avisos del servicio |
| `expand_alerts` | `false` | Muestra el texto de los avisos desplegado desde el principio, para un panel que no se toca |

```yaml
type: custom:renfe-tiempo-real-card
title: Cómo llegar al trabajo
departures_per_station: 3
show_platform: false
entities:
  - sensor.atocha_cercanias_next_departure
  - sensor.aranjuez_next_departure
```

### Cómo leer una fila

| Lo que ves | Qué es |
| --- | --- |
| `Atocha Cercanías` `18000` | Nombre y código de la estación |
| `C3` | Línea, con el color oficial que Renfe le da en ese núcleo |
| `Aranjuez` | Destino, es decir el final del recorrido de ese tren |
| `+1 min` | Retraso que informa Renfe frente al horario previsto. En rojo si va con demora, en verde y con signo menos si va adelantado. No se dibuja cuando es cero |
| `6` | Vía por la que se espera el tren |
| `4 min` | Espera hasta la salida: `Ahora` si sale ya, `<1 min` si falta menos de un minuto, y la hora del reloj si falta una hora o más |
| `6 min · 20 · 35` | La próxima salida con unidad y las siguientes del mismo trayecto sin repetirla |
| ♿ | Renfe marca ese tren como accesible |
| `6 avisos del servicio` | Avisos de Renfe que afectan a la estación o a sus líneas. Púlsalo para leerlos |

Comportamiento que conviene conocer:

- Se dibujan menos filas cuando Renfe devuelve menos salidas; no hay huecos de
  relleno para trenes que no existen.
- Cada fila es un trayecto (línea y destino). Una estación de paso como Atocha
  da servicio a los dos sentidos de la C3 desde un único código, así que genera
  una fila por sentido.
- `departures_per_station` cuenta salidas, no filas: con 5 salidas repartidas
  entre tres trayectos verás tres filas.
- Las esperas de una hora o más se muestran como hora del reloj en lugar de
  «797 min».
- El retraso solo se dibuja cuando no es cero: en rojo si el tren va con demora,
  en verde si va adelantado.
- El icono de accesibilidad aparece cuando Renfe marca el tren como accesible.
- Al pulsar una fila se abre el diálogo de información de esa estación.
- El texto, incluido el título por defecto, sigue el idioma de Home Assistant en
  español e inglés.
- La etiqueta «Actualizado hace x» se refresca cada 10 segundos sin consultar a
  Renfe.
- Si Renfe deja de regenerar el panel, el pie lo dice en rojo en lugar de mostrar
  el intervalo.

### Si prefieres un botón normal

El botón de la tarjeta es lo más cómodo, pero `renfe_tiempo_real.refresh` es una
acción normal, así que una tarjeta de botón también funciona:

```yaml
type: button
name: Actualizar Renfe
icon: mdi:refresh
tap_action:
  action: perform-action
  perform_action: renfe_tiempo_real.refresh
```

## Entidades

Cada estación configurada se convierte en un dispositivo. Las entidades de
trayecto se crean por **línea y destino**, porque una estación de paso da
servicio a los dos sentidos de la misma línea desde un único código.

| Entidad | Estado | Activada por defecto |
| --- | --- | --- |
| `sensor.<estación>_next_departure` | Minutos hasta el próximo tren de cualquier línea | sí |
| `sensor.<estación>_<línea>_<destino>` | Minutos hasta el próximo tren de esa línea y destino | sí |
| `sensor.<estación>_data_timestamp` | Fecha de los datos de Renfe, mostrada como «hace x minutos» | sí |
| `sensor.<estación>_service_alerts` | Número de avisos que afectan a la estación o a sus líneas | sí |
| `sensor.<estación>_<línea>_<destino>_time` | Hora absoluta de salida | no |

Los minutos se calculan contra el reloj del servidor de Renfe
(`fechaActualizacion`), no contra el reloj local, así que un desajuste de hora en
el equipo de Home Assistant no altera las estimaciones.

El atributo `realtime` indica si el tren ya ha salido de su origen. Mientras sea
`false`, la hora que publica Renfe es la del horario teórico y no una estimación
en tiempo real.

Una estación sin trenes previstos, que es lo normal de noche, conserva sus
entidades pero informa `unknown` y marca `in_service: false`. Una caída de Renfe
marca las entidades como `unavailable`.

El panel de Renfe incluye los trenes que **terminan** su recorrido en la
estación, y esos no se convierten en entidad: son la llegada del tren a su última
parada, no una salida a la que puedas subirte. El sensor de estación los cuenta
en el atributo `terminating_count` para que no desaparezcan sin dejar rastro.

El motivo está en cómo Renfe sirve los datos. El documento de una estación es un
recorte de un listado global de paradas: cada viaje aparece una vez por cada
estación en la que para, con la hora en esa estación y el **final del recorrido**
en `destino`. Así, el mismo `tripId` sale a las 16:51 en Aranjuez y a las 17:37
en Atocha, con `destino` Chamartín en las dos. Cuando la estación consultada es
justamente el final del recorrido, `destino` coincide con la propia estación y la
hora es la de llegada, sin salida posterior. El visor oficial no los filtra, así
que su panel muestra filas como «5 min · C7 · Madrid-Atocha Cercanías» estando en
Atocha.

### Frescura de los datos

Todos los sensores llevan estos atributos:

| Atributo | Significado |
| --- | --- |
| `station_code` | El código de la estación |
| `station_name` | El nombre de la estación en el catálogo |
| `nucleus` | El núcleo de Cercanías al que pertenece |
| `data_timestamp` | La fecha de los datos según el backend de Renfe |
| `last_polled` | Cuándo consultó Home Assistant *con éxito* por última vez, en hora local |
| `poll_interval_seconds` | El intervalo de actualización configurado |
| `in_service` | `false` cuando Renfe no publica panel para esta estación |
| `stale` | `true` cuando el panel lleva más de 5 minutos sin regenerarse |

`last_polled` solo avanza en consultas correctas, así que siempre responde a «qué
antigüedad tiene lo que estoy viendo». `sensor.<estación>_data_timestamp` existe
para poder ponerlo directamente en un panel: al ser un sensor de tipo
`timestamp`, la interfaz lo muestra como tiempo relativo.

Atributos de un sensor de trayecto:

```yaml
line: C3
line_colour: "#952585"
destination: Madrid-Chamartín-Clara Campoamor
destination_code: "17000"
departure_time: "2026-08-05T16:30:00+02:00"
scheduled_time: "2026-08-05T16:30:00+02:00"
delay: 0
platform: null
accessible: true
train: "20059"
status: null
realtime: false
next_departures:
  - line: C3
    line_colour: "#952585"
    destination: Madrid-Chamartín-Clara Campoamor
    destination_code: "17000"
    time: "2026-08-05T16:30:00+02:00"
    scheduled: "2026-08-05T16:30:00+02:00"
    minutes: 7
    delay: 0
    platform: null
    accessible: true
    train: "20059"
    status: null
station_code: "18000"
station_name: Atocha Cercanías
nucleus: Madrid
data_timestamp: "2026-08-05T16:22:41+02:00"
last_polled: "2026-08-05T16:22:43+02:00"
poll_interval_seconds: 180
in_service: true
stale: false
```

`status` es la posición que Renfe informa del tren: `at_station` parado en una
estación, `approaching` entrando en ella, `en_route` en marcha entre dos, y
`null` cuando todavía no ha salido de su origen.

### Ejemplo de automatización

```yaml
automation:
  - alias: Salir para el C3 a Chamartín
    triggers:
      - trigger: numeric_state
        entity_id: sensor.atocha_cercanias_c3_madrid_chamartin_clara_campoamor
        below: 8
    actions:
      - action: notify.mobile_app
        data:
          message: >
            El C3 a Chamartín sale en
            {{ states('sensor.atocha_cercanias_c3_madrid_chamartin_clara_campoamor') }}
            minutos por la vía
            {{ state_attr('sensor.atocha_cercanias_c3_madrid_chamartin_clara_campoamor', 'platform') }}.
```

## La API

No hay API documentada ni versionada. La integración lee los mismos documentos
JSON estáticos que descarga el visor oficial, todos bajo
`https://tiempo-real.renfe.com/`:

| Documento | Contenido |
| --- | --- |
| `data/estaciones.geojson` | Catálogo de las 879 estaciones: núcleo, coordenadas, líneas, accesibilidad y correspondencias |
| `renfe-json-cutter/write/salidas/estacion/<código>.json` | Panel de salidas de una estación, ventana móvil de unas dos horas |
| `renfe-visor/flota.json` | Posición y retraso de todos los trenes en servicio |
| `renfe-visor/alerts.json` | Avisos e informaciones por estación y por línea, en varios idiomas |

Detalles que la integración normaliza:

- El panel devuelve `404` cuando la estación no tiene trenes previstos. Eso es un
  estado, no un error, y no marca las entidades como no disponibles.
- El panel es un recorte por estación de un listado de paradas, así que incluye
  la última parada de cada viaje. Esas entradas se apartan del listado de
  salidas, como se explica más arriba.
- Las horas llegan como `dd-mm-yyyy HH:MM:SS` en el panel y como ISO-8601 sin
  desplazamiento en la flota. Ninguna trae zona horaria, así que se leen en
  `Europe/Madrid`.
- `accesible` vale `1`/`2` en el panel y es booleano en la flota.
- Los colores de línea vienen de `renfe-visor/lineas.geojson`, que son 1,5 MB de
  geometría de vía para 73 colores. La tabla está transcrita en `api.py` en lugar
  de descargarse.
- El propio JavaScript del visor apunta el panel de salidas y los avisos a
  `grt-nginx-visor-publico.desa.sir.renfe.es`, un servidor que no resuelve desde
  internet, por lo que el panel de salidas del visor oficial está roto. Las rutas
  equivalentes en `tiempo-real.renfe.com` sí funcionan y son las que se usan
  aquí.

## Desarrollo

```bash
uv venv --python 3.14
uv pip install pytest-homeassistant-custom-component ruff
.venv/bin/python -m pytest -q          # pruebas de la integración, red bloqueada
.venv/bin/ruff check custom_components tests scripts
.venv/bin/python scripts/live_check.py # comprobación contra la API real
```

Pruebas de la tarjeta, en jsdom:

```bash
npm install
npm test
```

Ganchos de `pre-commit` (ruff, gitleaks y comprobaciones de ficheros):

```bash
uv pip install pre-commit
.venv/bin/pre-commit install          # una vez, activa los ganchos
.venv/bin/pre-commit run --all-files  # revisa todo el repositorio
```

Validación con `hassfest`. Se monta solo `custom_components`: `hassfest` recorre
todo el directorio de trabajo buscando integraciones y, si encuentra el `.venv`,
valida también las cientos de integraciones de Home Assistant que hay dentro.

```bash
docker run --rm -v "$PWD/custom_components":/github/workspace/custom_components \
  ghcr.io/home-assistant/hassfest \
  --integration-path /github/workspace/custom_components/renfe_tiempo_real
```

## Datos

Los datos pertenecen a [Renfe](https://www.renfe.com). Los documentos que se
consultan no están documentados ni versionados, así que sé considerado con el
intervalo de consulta.

## Licencia

Código publicado bajo la licencia [MIT](LICENSE).

El logotipo de esta integración es obra propia y está cubierto por la licencia
MIT del proyecto. No es el logotipo de Renfe ni implica ninguna relación con
Renfe.

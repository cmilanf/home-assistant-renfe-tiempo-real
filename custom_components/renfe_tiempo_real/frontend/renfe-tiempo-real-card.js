/**
 * Renfe Tiempo Real card.
 *
 * One block per configured station showing the next departures grouped by line
 * and destination, the platform and delay Renfe reports, the time of the last
 * successful poll, and a button that triggers `renfe_tiempo_real.refresh`.
 *
 * Served by the Renfe integration itself, so no Lovelace resource has to be
 * registered by hand.
 *
 *   type: custom:renfe-tiempo-real-card
 *   title: Cercanías                # optional
 *   entities:                       # optional, auto-detected when omitted
 *     - sensor.atocha_cercanias_next_departure
 *   departures_per_station: 5       # optional, departures considered per station
 *   show_platform: true             # optional, show the platform column
 *   show_alerts: true               # optional, show the service alert notice
 *   expand_alerts: false            # optional, show the alert text unfolded
 */

const CARD_VERSION = "0.2.0";

const DEFAULT_DEPARTURES_PER_STATION = 5;
const MAX_DEPARTURES_PER_STATION = 12;

const FALLBACK_LINE_COLOUR = "#E6001E";

const TRANSLATIONS = {
  en: {
    name: "Renfe - Departures",
    description: "Next Renfe Cercanías departures for your stations.",
    noStations:
      "No Renfe station found. Add one in Settings → Devices & services.",
    unavailable: "Unavailable",
    noService: "No service",
    outOfService: "No trains due",
    now: "Now",
    lessThanAMinute: "<1 min",
    minutes: "min",
    polled: "Updated",
    never: "never",
    every: "every",
    refresh: "Refresh now",
    platform: "Platform",
    stationCode: "Station code",
    stale: "Renfe data is not being updated",
    secondsShort: "s",
    minutesShort: "min",
    hoursShort: "h",
    delayed: (value) => `+${value} min`,
    early: (value) => `${value} min`,
    delayedTitle: (value) => `Running ${value} min late`,
    earlyTitle: (value) => `Running ${value} min ahead of schedule`,
    departsAt: (value) => `Departs at ${value}`,
    accessible: "Accessible train",
    alerts: (count) => (count === 1 ? "1 service alert" : `${count} service alerts`),
    alertsToggle: "Show or hide the alert text",
    ago: (value) => `${value} ago`,
  },
  es: {
    name: "Renfe - Salidas",
    description: "Próximas salidas de Renfe Cercanías de tus estaciones.",
    noStations:
      "No se ha encontrado ninguna estación de Renfe. Añade una en Ajustes → Dispositivos y servicios.",
    unavailable: "No disponible",
    noService: "Sin servicio",
    outOfService: "Sin trenes previstos",
    now: "Ahora",
    lessThanAMinute: "<1 min",
    minutes: "min",
    polled: "Actualizado",
    never: "nunca",
    every: "cada",
    refresh: "Actualizar ahora",
    platform: "Vía",
    stationCode: "Código de estación",
    stale: "Renfe no está actualizando los datos",
    secondsShort: "s",
    minutesShort: "min",
    hoursShort: "h",
    delayed: (value) => `+${value} min`,
    early: (value) => `${value} min`,
    delayedTitle: (value) => `Lleva ${value} min de retraso`,
    earlyTitle: (value) => `Va ${value} min adelantado`,
    departsAt: (value) => `Sale a las ${value}`,
    accessible: "Tren accesible",
    alerts: (count) =>
      count === 1 ? "1 aviso del servicio" : `${count} avisos del servicio`,
    alertsToggle: "Mostrar u ocultar el texto del aviso",
    ago: (value) => `hace ${value}`,
  },
};

const REFRESH_ICON =
  "M17.65 6.35A7.958 7.958 0 0 0 12 4a8 8 0 1 0 7.73 10h-2.08A6 6 0 1 1 12 6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z";

const ACCESSIBLE_ICON =
  "M12 2a2 2 0 1 1-2 2 2 2 0 0 1 2-2m-2 5h4a1 1 0 0 1 1 1v4h3a1 1 0 0 1 0 2h-3.1a5 5 0 1 1-5.79-5.79V8a1 1 0 0 1 1-1m1 4.27A3 3 0 1 0 14.73 15H12a1 1 0 0 1-1-1z";

const ALERT_ICON = "M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z";

/** Contrast helper: white text on a dark badge, black on a light one. */
function useWhiteText(colour) {
  const hex = String(colour || "").replace("#", "");
  if (hex.length !== 6) {
    return true;
  }
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  if ([r, g, b].some((value) => Number.isNaN(value))) {
    return true;
  }
  // Renfe's own viewer uses the same luminance threshold for its line badges.
  return (r * 299 + g * 587 + b * 114) / 1000 < 150;
}

class RenfeCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._signature = null;
    this._tick = null;
    this._refreshing = false;
    // Alert notices the user flipped away from the `expand_alerts` default.
    this._toggled = new Set();
  }

  static getStubConfig() {
    return { type: "custom:renfe-tiempo-real-card" };
  }

  setConfig(config) {
    if (config.entities !== undefined && !Array.isArray(config.entities)) {
      throw new Error("`entities` must be a list of station sensors");
    }
    this._config = {
      departures_per_station: DEFAULT_DEPARTURES_PER_STATION,
      show_platform: true,
      show_alerts: true,
      expand_alerts: false,
      ...config,
    };
    this._signature = null;
    this._toggled = new Set();
    // Render straight away. The card picker builds the element and may measure
    // it before `hass` arrives, so waiting for hass leaves an empty element and
    // the preview never finishes loading.
    this._render();
  }

  /** How many departures to consider per station, clamped to something sensible. */
  get _departuresPerStation() {
    const requested = Number(this._config.departures_per_station);
    if (!Number.isFinite(requested)) {
      return DEFAULT_DEPARTURES_PER_STATION;
    }
    return Math.min(
      MAX_DEPARTURES_PER_STATION,
      Math.max(1, Math.trunc(requested))
    );
  }

  getCardSize() {
    if (!this._hass) {
      return 3;
    }
    return 1 + Math.max(1, this._stations().length) * 2;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  connectedCallback() {
    // Keeps the "updated 2 min ago" label honest between polls.
    this._tick = window.setInterval(() => this._renderFooter(), 10000);
  }

  disconnectedCallback() {
    if (this._tick) {
      window.clearInterval(this._tick);
      this._tick = null;
    }
  }

  get _t() {
    const language =
      (this._hass &&
        (this._hass.language || (this._hass.locale || {}).language)) ||
      "en";
    return TRANSLATIONS[language.split("-")[0]] || TRANSLATIONS.en;
  }

  /** Return the station level sensors to show, in display order. */
  _stations() {
    if (!this._hass) {
      return [];
    }
    const states = this._hass.states;
    let ids;
    if (this._config.entities && this._config.entities.length) {
      ids = this._config.entities.map((item) =>
        typeof item === "string" ? item : item.entity
      );
    } else {
      // A Renfe station sensor is the only one carrying both of these.
      ids = Object.keys(states).filter((id) => {
        const attributes = states[id].attributes || {};
        return (
          id.startsWith("sensor.") &&
          typeof attributes.station_code === "string" &&
          Array.isArray(attributes.departures)
        );
      });
    }
    const stations = ids
      .filter((id) => states[id])
      .map((id) => ({ id, state: states[id] }));
    if (!this._config.entities) {
      stations.sort((a, b) =>
        this._stationName(a.state).localeCompare(this._stationName(b.state))
      );
    }
    return stations;
  }

  _stationName(state) {
    const attributes = state.attributes || {};
    return (
      attributes.station_name ||
      (attributes.friendly_name || "").replace(
        /\s*(Next departure|Próxima salida)$/i,
        ""
      ) ||
      state.entity_id
    );
  }

  /** Find the alert sensor that belongs to the same station, when enabled. */
  _alertSensor(stationCode) {
    if (!this._config.show_alerts || !this._hass) {
      return null;
    }
    const states = this._hass.states;
    for (const id of Object.keys(states)) {
      const attributes = states[id].attributes || {};
      if (
        id.startsWith("sensor.") &&
        attributes.station_code === stationCode &&
        Array.isArray(attributes.alerts)
      ) {
        return states[id];
      }
    }
    return null;
  }

  /** Render an ISO timestamp as a local clock time, or "" when unusable. */
  _clock(iso) {
    if (!iso) {
      return "";
    }
    const when = new Date(iso);
    if (Number.isNaN(when.getTime())) {
      return "";
    }
    return when.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  /** Whether the alert text of a station is currently expanded. */
  _alertsExpanded(entityId) {
    const byDefault = Boolean(this._config.expand_alerts);
    return this._toggled.has(entityId) ? !byDefault : byDefault;
  }

  /** Format minutes for display, falling back to a clock time when far away. */
  _formatWait(minutes, isoTime, compact = false) {
    const t = this._t;
    if (minutes === null || minutes === undefined) {
      return t.noService;
    }
    if (minutes >= 60) {
      const clock = this._clock(isoTime);
      if (clock) {
        return clock;
      }
    }
    if (minutes <= 0) {
      return compact ? "0" : t.now;
    }
    if (minutes < 1) {
      return compact ? "<1" : t.lessThanAMinute;
    }
    return compact
      ? `${Math.round(minutes)}`
      : `${Math.round(minutes)} ${t.minutes}`;
  }

  _relativeAge(iso) {
    const t = this._t;
    if (!iso) {
      return t.never;
    }
    const when = new Date(iso);
    if (Number.isNaN(when.getTime())) {
      return t.never;
    }
    const seconds = Math.max(
      0,
      Math.round((Date.now() - when.getTime()) / 1000)
    );
    if (seconds < 60) {
      return t.ago(`${seconds} ${t.secondsShort}`);
    }
    if (seconds < 3600) {
      return t.ago(`${Math.round(seconds / 60)} ${t.minutesShort}`);
    }
    return t.ago(`${Math.round(seconds / 3600)} ${t.hoursShort}`);
  }

  _escape(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  _build() {
    const root = this.shadowRoot;
    root.innerHTML = `
      <style>
        ha-card { padding: 0; overflow: hidden; }
        .header {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 12px 16px 4px 16px;
        }
        .title {
          flex: 1;
          font-size: 1.2em;
          font-weight: 500;
          color: var(--ha-card-header-color, var(--primary-text-color));
        }
        button.refresh {
          all: unset;
          cursor: pointer;
          border-radius: 50%;
          padding: 6px;
          line-height: 0;
          color: var(--secondary-text-color);
        }
        button.refresh:hover { background: var(--secondary-background-color); }
        button.refresh:focus-visible { outline: 2px solid var(--primary-color); }
        button.refresh[disabled] { opacity: 0.5; cursor: default; }
        button.refresh svg { width: 22px; height: 22px; fill: currentColor; }
        button.refresh.spinning svg { animation: spin 1s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .stations { padding: 4px 0 4px 0; }
        .station {
          display: flex;
          align-items: baseline;
          gap: 8px;
          padding: 10px 16px 2px 16px;
          cursor: pointer;
        }
        .station:hover { background: var(--secondary-background-color); }
        .station:focus-visible { outline: 2px solid var(--primary-color); }
        .station-code {
          flex: 0 0 auto;
          font-size: 0.8em;
          font-variant-numeric: tabular-nums;
          color: var(--secondary-text-color);
          border: 1px solid var(--divider-color, #e0e0e0);
          border-radius: 4px;
          padding: 0 5px;
        }
        .badge {
          flex: 0 0 auto;
          min-width: 2.6em;
          text-align: center;
          font-weight: 700;
          font-size: 0.85em;
          padding: 3px 6px;
          border-radius: 4px;
        }
        .labels { flex: 1; min-width: 0; }
        .name {
          flex: 1;
          min-width: 0;
          font-weight: 500;
          color: var(--primary-text-color);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .destination {
          font-size: 0.9em;
          color: var(--secondary-text-color);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .destination svg {
          width: 13px;
          height: 13px;
          fill: currentColor;
          vertical-align: -2px;
          margin-left: 4px;
        }
        .delay { color: var(--error-color, #db4437); }
        .early { color: var(--success-color, #43a047); }
        .platform {
          flex: 0 0 auto;
          font-size: 0.8em;
          font-variant-numeric: tabular-nums;
          color: var(--secondary-text-color);
          border: 1px solid var(--divider-color, #e0e0e0);
          border-radius: 4px;
          padding: 0 5px;
        }
        .wait {
          flex: 0 0 auto;
          font-weight: 500;
          font-variant-numeric: tabular-nums;
          color: var(--primary-text-color);
        }
        .wait-more { font-weight: 400; color: var(--secondary-text-color); }
        .route {
          display: flex;
          gap: 10px;
          align-items: center;
          padding: 3px 16px;
        }
        .notice {
          display: flex;
          gap: 6px;
          align-items: center;
          padding: 2px 16px 4px 16px;
          font-size: 0.82em;
          color: var(--warning-color, #ffa600);
          cursor: pointer;
        }
        .notice svg { width: 14px; height: 14px; fill: currentColor; }
        .notice .chevron { font-size: 0.7em; opacity: 0.7; }
        .notice-texts { padding: 0 16px 6px 36px; }
        .notice-text {
          font-size: 0.82em;
          line-height: 1.4;
          color: var(--secondary-text-color);
          margin-bottom: 6px;
        }
        .notice-target {
          font-weight: 600;
          margin-right: 6px;
          color: var(--warning-color, #ffa600);
        }
        .footer {
          display: flex;
          justify-content: space-between;
          gap: 8px;
          padding: 8px 16px 12px 16px;
          font-size: 0.8em;
          color: var(--secondary-text-color);
        }
        .footer .stale { color: var(--error-color, #db4437); }
        .empty { padding: 16px; color: var(--secondary-text-color); }
      </style>
      <ha-card>
        <div class="header">
          <div class="title"></div>
          <button class="refresh" type="button">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="${REFRESH_ICON}"></path></svg>
          </button>
        </div>
        <div class="stations"></div>
        <div class="footer"><span class="polled"></span><span class="interval"></span></div>
      </ha-card>
    `;
    this._titleElement = root.querySelector(".title");
    this._stationsElement = root.querySelector(".stations");
    this._polledElement = root.querySelector(".polled");
    this._intervalElement = root.querySelector(".interval");
    this._button = root.querySelector("button.refresh");
    this._button.addEventListener("click", () => this._refresh());
  }

  _render() {
    if (!this.shadowRoot.firstElementChild) {
      this._build();
    }
    const t = this._t;
    const title = this._config.title === undefined ? t.name : this._config.title;
    this._titleElement.textContent = title || "";
    this._titleElement.style.display = title ? "" : "none";
    this._button.title = t.refresh;
    this._button.setAttribute("aria-label", t.refresh);

    const stations = this._stations();
    // Only rebuild the rows when something actually changed, to avoid flicker.
    // `hass` presence is part of the signature: the first render happens in
    // setConfig without it, and going from "no hass" to "hass with no stations"
    // must still swap the placeholder for the empty state message.
    const signature = JSON.stringify([
      Boolean(this._hass),
      this._departuresPerStation,
      this._config.show_platform,
      this._config.show_alerts,
      this._config.expand_alerts,
      [...this._toggled].sort(),
      title,
      stations.map((station) => {
        const alerts = this._alertSensor(station.state.attributes.station_code);
        return [
          station.id,
          station.state.state,
          station.state.last_updated,
          // The alert sensor is a sibling entity, so its own changes have to
          // invalidate the block too.
          alerts ? [alerts.state, alerts.last_updated] : null,
        ];
      }),
    ]);
    if (signature !== this._signature) {
      this._signature = signature;
      this._renderStations(stations);
    }
    this._renderFooter();
  }

  _renderStations(stations) {
    const t = this._t;
    if (!this._hass) {
      // Waiting for the first hass object. Keep the shell visible so the card
      // has a size, but do not claim there are no stations yet.
      this._stationsElement.innerHTML = "";
      return;
    }
    if (!stations.length) {
      this._stationsElement.innerHTML = `<div class="empty">${this._escape(
        t.noStations
      )}</div>`;
      return;
    }

    this._stationsElement.innerHTML = stations
      .map((station) => this._stationBlock(station))
      .join("");

    this._stationsElement.querySelectorAll("[data-entity]").forEach((row) => {
      const entityId = row.getAttribute("data-entity");
      row.addEventListener("click", () => this._moreInfo(entityId));
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          this._moreInfo(entityId);
        }
      });
    });

    this._stationsElement.querySelectorAll("[data-alerts]").forEach((row) => {
      const entityId = row.getAttribute("data-alerts");
      const toggle = () => {
        if (this._toggled.has(entityId)) {
          this._toggled.delete(entityId);
        } else {
          this._toggled.add(entityId);
        }
        // The expansion is part of the signature, so force a rebuild.
        this._signature = null;
        this._render();
      };
      row.addEventListener("click", toggle);
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          toggle();
        }
      });
    });
  }

  _stationBlock(station) {
    const t = this._t;
    const attributes = station.state.attributes || {};
    const departures = Array.isArray(attributes.departures)
      ? attributes.departures
      : [];
    const unavailable = station.state.state === "unavailable";

    const rows = [
      `<div class="station" data-entity="${this._escape(
        station.id
      )}" role="button" tabindex="0">
        <div class="name">${this._escape(this._stationName(station.state))}</div>
        <div class="station-code" title="${this._escape(t.stationCode)}">${this._escape(
          attributes.station_code || ""
        )}</div>
      </div>`,
    ];

    const alerts = this._alertSensor(attributes.station_code);
    const alertCount = alerts ? Number(alerts.state) : 0;
    if (alerts && Number.isFinite(alertCount) && alertCount > 0) {
      const texts = Array.isArray(alerts.attributes.alerts)
        ? alerts.attributes.alerts
        : [];
      const expanded = this._alertsExpanded(alerts.entity_id);
      rows.push(
        `<div class="notice" data-alerts="${this._escape(alerts.entity_id)}"
              role="button" tabindex="0" aria-expanded="${expanded}"
              title="${this._escape(t.alertsToggle)}">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="${ALERT_ICON}"></path></svg>
          <span>${this._escape(t.alerts(alertCount))}</span>
          <span class="chevron" aria-hidden="true">${expanded ? "▲" : "▼"}</span>
        </div>`
      );
      if (expanded && texts.length) {
        rows.push(
          `<div class="notice-texts">${texts
            .map(
              (alert) =>
                `<div class="notice-text"><span class="notice-target">${this._escape(
                  alert.target || ""
                )}</span>${this._escape(alert.text || "")}</div>`
            )
            .join("")}</div>`
        );
      }
    }

    const groups = this._groupByRoute(
      departures.slice(0, this._departuresPerStation)
    );

    if (!groups.length) {
      let message = t.noService;
      if (unavailable) {
        message = t.unavailable;
      } else if (attributes.in_service === false) {
        message = t.outOfService;
      }
      rows.push(
        `<div class="route"><div class="labels"><div class="destination">${this._escape(
          message
        )}</div></div></div>`
      );
      return rows.join("");
    }

    for (const group of groups) {
      const [first, ...rest] = group.departures;
      // Follow ups drop the unit so the row stays narrow: "7 min · 22 · 37".
      const more = rest
        .map((departure) =>
          this._formatWait(departure.minutes, departure.time, true)
        )
        .join(" · ");
      const colour = group.colour || FALLBACK_LINE_COLOUR;
      const textColour = useWhiteText(colour) ? "#fff" : "#000";
      const clock = this._clock(first.time);
      rows.push(
        `<div class="route">
          <span class="badge" style="background:${this._escape(
            colour
          )};color:${textColour}">${this._escape(group.line || "—")}</span>
          <div class="labels"><div class="destination">${this._escape(
            group.destination
          )}${this._delayLabel(first)}${
            first.accessible
              ? `<svg viewBox="0 0 24 24" aria-hidden="true"><title>${this._escape(
                  t.accessible
                )}</title><path d="${ACCESSIBLE_ICON}"></path></svg>`
              : ""
          }</div></div>
          ${
            this._config.show_platform && first.platform
              ? `<div class="platform" title="${this._escape(
                  t.platform
                )}">${this._escape(first.platform)}</div>`
              : ""
          }
          <div class="wait"${
            clock ? ` title="${this._escape(t.departsAt(clock))}"` : ""
          }>${this._escape(this._formatWait(first.minutes, first.time))}${
            more ? `<span class="wait-more"> · ${this._escape(more)}</span>` : ""
          }</div>
        </div>`
      );
    }
    return rows.join("");
  }

  /** Render the delay Renfe reports, when there is one worth showing. */
  _delayLabel(departure) {
    const t = this._t;
    const delay = Number(departure.delay);
    if (!Number.isFinite(delay) || delay === 0) {
      return "";
    }
    const late = delay > 0;
    const label = late ? t.delayed(delay) : t.early(delay);
    const title = late ? t.delayedTitle(delay) : t.earlyTitle(Math.abs(delay));
    return ` <span class="${late ? "delay" : "early"}" title="${this._escape(
      title
    )}">${this._escape(label)}</span>`;
  }

  /** Collapse consecutive departures of the same line and destination. */
  _groupByRoute(departures) {
    const groups = [];
    const index = new Map();
    for (const departure of departures) {
      // A station serves both directions of a line from one code, so the
      // destination is what separates one journey from the other.
      const key = `${departure.line}__${
        departure.destination_code || departure.destination
      }`;
      let group = index.get(key);
      if (!group) {
        group = {
          line: departure.line,
          colour: departure.line_colour,
          destination: departure.destination || departure.destination_code || "",
          departures: [],
        };
        index.set(key, group);
        groups.push(group);
      }
      group.departures.push(departure);
    }
    return groups;
  }

  _renderFooter() {
    if (!this._polledElement) {
      return;
    }
    const t = this._t;
    if (!this._hass) {
      this._polledElement.textContent = "";
      this._intervalElement.textContent = "";
      return;
    }
    const stations = this._stations();
    // The most recent successful poll across the stations on this card.
    let polled = null;
    let interval = null;
    let stale = false;
    for (const station of stations) {
      const attributes = station.state.attributes || {};
      if (attributes.last_polled) {
        const when = new Date(attributes.last_polled);
        if (!Number.isNaN(when.getTime()) && (!polled || when > polled)) {
          polled = when;
        }
      }
      if (attributes.poll_interval_seconds) {
        interval = Math.max(interval || 0, attributes.poll_interval_seconds);
      }
      if (attributes.stale) {
        stale = true;
      }
    }
    this._polledElement.textContent = `${t.polled} ${this._relativeAge(
      polled ? polled.toISOString() : null
    )}`;
    if (polled) {
      this._polledElement.title = polled.toLocaleString();
    }
    this._intervalElement.textContent = stale
      ? t.stale
      : interval
        ? `${t.every} ${interval} ${t.secondsShort}`
        : "";
    // toggle, not className: the element keeps the class the selectors use.
    this._intervalElement.classList.toggle("stale", stale);
  }

  _moreInfo(entityId) {
    this.dispatchEvent(
      new CustomEvent("hass-more-info", {
        detail: { entityId },
        bubbles: true,
        composed: true,
      })
    );
  }

  async _refresh() {
    if (!this._hass || this._refreshing) {
      return;
    }
    this._refreshing = true;
    this._button.disabled = true;
    this._button.classList.add("spinning");
    const stations = this._stations();
    // Target only the stations on this card when the user listed them.
    const target =
      this._config.entities && stations.length
        ? { entity_id: stations.map((station) => station.id) }
        : {};
    try {
      await this._hass.callService("renfe_tiempo_real", "refresh", {}, target);
    } finally {
      this._refreshing = false;
      this._button.disabled = false;
      this._button.classList.remove("spinning");
      this._renderFooter();
    }
  }
}

if (!customElements.get("renfe-tiempo-real-card")) {
  customElements.define("renfe-tiempo-real-card", RenfeCard);
}

window.customCards = window.customCards || [];
if (
  !window.customCards.some((card) => card.type === "renfe-tiempo-real-card")
) {
  window.customCards.push({
    type: "renfe-tiempo-real-card",
    name: TRANSLATIONS.en.name,
    description: TRANSLATIONS.en.description,
    preview: true,
    documentationURL:
      "https://github.com/cmilanf/home-assistant-renfe-tiempo-real",
  });
}

console.info(
  `%c RENFE-TIEMPO-REAL-CARD %c ${CARD_VERSION} `,
  "background:#E6001E;color:#fff",
  ""
);

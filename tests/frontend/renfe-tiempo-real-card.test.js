/**
 * Tests for the Renfe Tiempo Real Lovelace card, run in jsdom.
 *
 *   npm install
 *   npm test
 *
 * The card is plain DOM with no build step, so it can be exercised directly.
 */

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test, beforeEach, afterEach } = require("node:test");
const { JSDOM } = require("jsdom");

const CARD_PATH = path.join(
  __dirname,
  "..",
  "..",
  "custom_components",
  "renfe_tiempo_real",
  "frontend",
  "renfe-tiempo-real-card.js"
);

const ATOCHA = "sensor.atocha_cercanias_next_departure";
const ATOCHA_ALERTS = "sensor.atocha_cercanias_service_alerts";
const ARANJUEZ = "sensor.aranjuez_next_departure";

const CHAMARTIN = "Madrid-Chamartín-Clara Campoamor";
const ALCALA = "Alcalá de Henares";

const C3 = "#952585";
const C7 = "#E5202A";

/** Six upcoming trains across three routes, in time order as Renfe returns them. */
function departures() {
  return [
    { line: "C7", line_colour: C7, destination: ALCALA, destination_code: "70103", minutes: 1, delay: 4, platform: "3", accessible: false, time: "2026-08-05T16:24:00+02:00" },
    { line: "C3", line_colour: C3, destination: "Aranjuez", destination_code: "60200", minutes: 3, delay: 0, platform: "7", accessible: true, time: "2026-08-05T16:26:00+02:00" },
    { line: "C3", line_colour: C3, destination: CHAMARTIN, destination_code: "17000", minutes: 7, delay: 0, platform: "", accessible: true, time: "2026-08-05T16:30:00+02:00" },
    { line: "C3", line_colour: C3, destination: CHAMARTIN, destination_code: "17000", minutes: 22, delay: 7, platform: "5", accessible: false, time: "2026-08-05T16:45:00+02:00" },
    { line: "C7", line_colour: C7, destination: ALCALA, destination_code: "70103", minutes: 26, delay: 0, platform: "3", accessible: true, time: "2026-08-05T16:49:00+02:00" },
    { line: "C3", line_colour: C3, destination: CHAMARTIN, destination_code: "17000", minutes: 30, delay: -3, platform: "5", accessible: true, time: "2026-08-05T16:53:00+02:00" },
  ];
}

function makeHass(overrides = {}) {
  const calls = [];
  const polled = new Date(Date.now() - 42000).toISOString();
  const hass = {
    language: "en",
    states: {
      [ATOCHA]: {
        entity_id: ATOCHA,
        state: "1",
        last_updated: "2026-08-05T16:22:45+02:00",
        attributes: {
          friendly_name: "Atocha Cercanías Next departure",
          station_code: "18000",
          station_name: "Atocha Cercanías",
          nucleus: "Madrid",
          last_polled: polled,
          poll_interval_seconds: 120,
          in_service: true,
          stale: false,
          departure_count: 6,
          departures: departures(),
        },
      },
      // Not a Renfe sensor, must be ignored by auto detection.
      "sensor.kitchen_temperature": {
        entity_id: "sensor.kitchen_temperature",
        state: "21",
        attributes: { friendly_name: "Kitchen temperature" },
      },
      // A Renfe *route* sensor. It carries station_code too, so auto detection
      // must discriminate on the departures list or every route would be a row.
      "sensor.atocha_cercanias_c3_aranjuez": {
        entity_id: "sensor.atocha_cercanias_c3_aranjuez",
        state: "3",
        attributes: {
          friendly_name: "Atocha Cercanías C3 → Aranjuez",
          station_code: "18000",
          line: "C3",
          next_departures: [{ line: "C3", minutes: 3 }],
        },
      },
    },
    callService(domain, service, data, target) {
      calls.push({ domain, service, data, target });
      return Promise.resolve();
    },
    ...overrides,
  };
  hass._calls = calls;
  return hass;
}

/** Add the sibling alert sensor of Atocha to a hass object. */
function withAlerts(hass, count = 2) {
  hass.states[ATOCHA_ALERTS] = {
    entity_id: ATOCHA_ALERTS,
    state: String(count),
    attributes: {
      friendly_name: "Atocha Cercanías Service alerts",
      station_code: "18000",
      warning_count: count,
      information_count: 0,
      alerts: [{ scope: "station", kind: "warning", target: "18000", text: "Lift out of service" }],
    },
  };
  return hass;
}

let dom;
let RenfeCard;
let mounted;

beforeEach(() => {
  dom = new JSDOM("<!doctype html><html><body></body></html>", {
    url: "http://localhost/",
  });
  global.window = dom.window;
  global.document = dom.window.document;
  global.HTMLElement = dom.window.HTMLElement;
  global.customElements = dom.window.customElements;
  global.CustomEvent = dom.window.CustomEvent;
  mounted = [];
  const source = fs.readFileSync(CARD_PATH, "utf8");
  // Evaluate inside the jsdom window so customElements registration lands there.
  dom.window.eval(source);
  RenfeCard = dom.window.customElements.get("renfe-tiempo-real-card");
});

afterEach(() => {
  // The card starts an interval to keep the "updated x ago" label fresh.
  // Detaching it and closing the window clears every pending timer, otherwise
  // the node process would never exit.
  for (const card of mounted) {
    card.remove();
  }
  mounted = [];
  dom.window.close();
});

function mount(config = {}, hass = makeHass()) {
  const card = new RenfeCard();
  card.setConfig({ type: "custom:renfe-tiempo-real-card", ...config });
  dom.window.document.body.appendChild(card);
  card.hass = hass;
  mounted.push(card);
  return card;
}

const routeRows = (card) => [...card.shadowRoot.querySelectorAll(".route")];
const text = (node, selector) => node.querySelector(selector).textContent;

test("renders its shell on setConfig, before hass is ever set", () => {
  // The card picker creates the element and measures it before handing over a
  // hass object. If nothing renders, the preview spinner never finishes.
  const card = new RenfeCard();
  card.setConfig({ type: "custom:renfe-tiempo-real-card" });
  dom.window.document.body.appendChild(card);
  mounted.push(card);

  assert.ok(card.shadowRoot.querySelector("ha-card"), "ha-card is present");
  assert.ok(card.shadowRoot.querySelector("button.refresh"), "button is present");
  assert.equal(text(card.shadowRoot, ".title"), "Renfe - Departures");
  assert.equal(card.shadowRoot.querySelector(".empty"), null);
  assert.equal(card.getCardSize(), 3);

  card.hass = makeHass();
  assert.equal(card.shadowRoot.querySelectorAll(".station").length, 1);
});

test("registers the custom element and the card picker entry", () => {
  assert.ok(RenfeCard, "renfe-tiempo-real-card is defined");
  const listed = dom.window.customCards.find(
    (card) => card.type === "renfe-tiempo-real-card"
  );
  assert.ok(listed, "card is offered in the picker");
  assert.equal(listed.preview, true);
  assert.match(listed.description, /Renfe Cercan/);
});

test("shows the station name and its code", () => {
  const card = mount();
  const header = card.shadowRoot.querySelector(".station");

  assert.match(text(header, ".name"), /Atocha Cercan/);
  assert.equal(text(header, ".station-code"), "18000");
});

test("groups departures of the same line and destination into one row", () => {
  const card = mount();
  const rows = routeRows(card);

  // Six departures across three routes, capped at five, become three rows.
  assert.equal(rows.length, 3);
  assert.equal(text(rows[0], ".badge"), "C7");
  assert.match(text(rows[0], ".destination"), /Alcal/);
  assert.equal(text(rows[1], ".badge"), "C3");
  assert.match(text(rows[1], ".destination"), /Aranjuez/);
  assert.equal(text(rows[2], ".badge"), "C3");
  assert.match(text(rows[2], ".destination"), /Chamart/);
});

test("colours the line badge with the official Renfe colour", () => {
  const card = mount();
  const badges = card.shadowRoot.querySelectorAll(".route .badge");

  // rgb() is how jsdom normalises a hex background.
  assert.equal(badges[0].style.background, "rgb(229, 32, 42)");
  assert.equal(badges[1].style.background, "rgb(149, 37, 133)");
  // C7 red is dark, so its label is white.
  assert.equal(badges[0].style.color, "rgb(255, 255, 255)");
});

test("falls back to the corporate red when no colour is published", () => {
  const hass = makeHass();
  for (const departure of hass.states[ATOCHA].attributes.departures) {
    delete departure.line_colour;
  }
  const card = mount({}, hass);

  assert.equal(
    card.shadowRoot.querySelector(".route .badge").style.background,
    "rgb(230, 0, 30)"
  );
});

test("lists the follow ups without repeating the unit", () => {
  const card = mount();
  const rows = routeRows(card);

  // First five departures only: C7 at 1/26, C3 Aranjuez at 3, C3 Chamartín 7/22.
  assert.equal(text(rows[0], ".wait"), "1 min · 26");
  assert.equal(text(rows[1], ".wait"), "3 min");
  assert.equal(text(rows[2], ".wait"), "7 min · 22");
  // The sixth departure, C3 Chamartín at 30 minutes, is beyond the limit.
  assert.doesNotMatch(text(rows[2], ".wait"), /30/);
  assert.equal(text(rows[0], ".wait-more"), " · 26");
});

test("departures_per_station limits how many departures are considered", () => {
  let rows = routeRows(mount({ departures_per_station: 1 }));
  assert.equal(rows.length, 1);
  assert.equal(text(rows[0], ".wait"), "1 min");

  rows = routeRows(mount({ departures_per_station: 3 }));
  assert.equal(rows.length, 3);
  assert.equal(text(rows[0], ".wait"), "1 min");

  rows = routeRows(mount({ departures_per_station: 12 }));
  assert.equal(text(rows[2], ".wait"), "7 min · 22 · 30");
});

test("clamps nonsense departures_per_station values", () => {
  assert.equal(routeRows(mount({ departures_per_station: 0 })).length, 1);
  assert.equal(routeRows(mount({ departures_per_station: -5 })).length, 1);
  // Not a number at all: fall back to the default of five.
  assert.equal(routeRows(mount({ departures_per_station: "abc" })).length, 3);
});

test("shows the platform Renfe reports, and hides it on request", () => {
  let card = mount();
  assert.equal(text(routeRows(card)[0], ".platform"), "3");
  // The C3 to Chamartín has no platform assigned yet.
  assert.equal(routeRows(card)[2].querySelector(".platform"), null);

  card = mount({ show_platform: false });
  assert.equal(card.shadowRoot.querySelector(".platform"), null);
});

test("shows the delay of a late train and the lead of an early one", () => {
  const card = mount({ departures_per_station: 12 });
  const rows = routeRows(card);

  assert.equal(text(rows[0], ".delay"), "+4 min");
  // The C3 to Aranjuez is on time, so nothing is shown.
  assert.equal(rows[1].querySelector(".delay"), null);
  assert.equal(rows[1].querySelector(".early"), null);
});

test("marks an accessible train and leaves the others plain", () => {
  const card = mount();
  const rows = routeRows(card);

  // The first C7 is not accessible, the C3 to Aranjuez is.
  assert.equal(rows[0].querySelector(".destination svg"), null);
  assert.ok(rows[1].querySelector(".destination svg"));
});

test("shows the service alerts of the station when there are any", () => {
  const card = mount({}, withAlerts(makeHass()));
  const notice = card.shadowRoot.querySelector(".notice");

  assert.ok(notice);
  assert.match(notice.textContent, /2 service alerts/);

  // One alert is singular.
  const single = mount({}, withAlerts(makeHass(), 1));
  assert.match(single.shadowRoot.querySelector(".notice").textContent, /1 service alert/);
});

test("hides the alert notice when there is none or it is turned off", () => {
  assert.equal(mount({}, withAlerts(makeHass(), 0)).shadowRoot.querySelector(".notice"), null);
  assert.equal(
    mount({ show_alerts: false }, withAlerts(makeHass())).shadowRoot.querySelector(".notice"),
    null
  );
});

test("auto detects several stations and sorts them by name", () => {
  const hass = makeHass();
  hass.states[ARANJUEZ] = {
    entity_id: ARANJUEZ,
    state: "12",
    last_updated: "2026-08-05T16:22:45+02:00",
    attributes: {
      friendly_name: "Aranjuez Next departure",
      station_code: "60200",
      station_name: "Aranjuez",
      last_polled: new Date().toISOString(),
      poll_interval_seconds: 120,
      in_service: true,
      departures: [
        { line: "C3", line_colour: C3, destination: CHAMARTIN, destination_code: "17000", minutes: 12, delay: 0, platform: "1", accessible: true, time: null },
      ],
    },
  };
  const card = mount({}, hass);
  const names = [...card.shadowRoot.querySelectorAll(".station .name")].map(
    (node) => node.textContent
  );

  assert.deepEqual(names, ["Aranjuez", "Atocha Cercanías"]);
});

test("ignores non Renfe sensors and Renfe route sensors when auto detecting", () => {
  const card = mount();

  assert.equal(card.shadowRoot.querySelectorAll(".station").length, 1);
  assert.match(text(card.shadowRoot, ".station .name"), /Atocha/);
});

test("honours an explicit entities list", () => {
  const card = mount({ entities: [ATOCHA] });
  assert.equal(card.shadowRoot.querySelectorAll(".station").length, 1);
});

test("shows the time of day instead of minutes for distant departures", () => {
  const hass = makeHass();
  hass.states[ATOCHA].attributes.departures = [
    {
      line: "C3",
      line_colour: C3,
      destination: CHAMARTIN,
      destination_code: "17000",
      time: "2026-08-06T05:40:00+02:00",
      minutes: 797,
      delay: 0,
      platform: "1",
      accessible: true,
    },
  ];
  const card = mount({}, hass);
  const wait = text(routeRows(card)[0], ".wait");

  assert.doesNotMatch(wait, /797/);
  assert.match(wait, /\d{1,2}[:.]\d{2}/);
});

test("distinguishes an outage from a station with no train due", () => {
  let hass = makeHass();
  hass.states[ATOCHA].state = "unavailable";
  hass.states[ATOCHA].attributes.departures = [];
  let card = mount({}, hass);
  // The station is still listed, so the user knows it is configured.
  assert.equal(card.shadowRoot.querySelectorAll(".station").length, 1);
  assert.equal(text(routeRows(card)[0], ".destination"), "Unavailable");

  hass = makeHass();
  hass.states[ATOCHA].state = "unknown";
  hass.states[ATOCHA].attributes.departures = [];
  hass.states[ATOCHA].attributes.in_service = false;
  card = mount({}, hass);
  assert.equal(text(routeRows(card)[0], ".destination"), "No trains due");
});

test("renders the empty state when no station is configured", () => {
  const hass = makeHass();
  delete hass.states[ATOCHA];
  const card = mount({}, hass);

  assert.equal(card.shadowRoot.querySelectorAll(".station").length, 0);
  assert.match(text(card.shadowRoot, ".empty"), /No Renfe station/);
});

test("shows the last poll as a relative age plus the interval", () => {
  const card = mount();

  assert.match(text(card.shadowRoot, ".polled"), /Updated 4\d s ago/);
  assert.equal(text(card.shadowRoot, ".interval"), "every 120 s");
});

test("warns in the footer when Renfe stops updating the board", () => {
  const hass = makeHass();
  hass.states[ATOCHA].attributes.stale = true;
  const card = mount({}, hass);

  assert.match(text(card.shadowRoot, ".interval"), /not being updated/);
});

test("the button calls renfe_tiempo_real.refresh", async () => {
  const hass = makeHass();
  const card = mount({}, hass);

  card.shadowRoot.querySelector("button.refresh").click();
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(hass._calls.length, 1);
  assert.deepEqual(hass._calls[0], {
    domain: "renfe_tiempo_real",
    service: "refresh",
    data: {},
    target: {},
  });
});

test("the button targets only the listed stations", async () => {
  const hass = makeHass();
  const card = mount({ entities: [ATOCHA] }, hass);

  card.shadowRoot.querySelector("button.refresh").click();
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.deepEqual(hass._calls[0].target, { entity_id: [ATOCHA] });
});

test("clicking the station opens the more info dialog", () => {
  const card = mount();
  let detail = null;
  card.addEventListener("hass-more-info", (event) => {
    detail = event.detail;
  });

  card.shadowRoot.querySelector(".station").click();

  assert.deepEqual(detail, { entityId: ATOCHA });
});

test("clicking the alert notice unfolds the alert text", () => {
  const card = mount({}, withAlerts(makeHass()));
  const notice = card.shadowRoot.querySelector(".notice");

  // Collapsed by default: the summary only.
  assert.equal(notice.getAttribute("aria-expanded"), "false");
  assert.equal(card.shadowRoot.querySelector(".notice-texts"), null);

  notice.click();

  const texts = card.shadowRoot.querySelector(".notice-texts");
  assert.ok(texts, "the alert text is shown");
  assert.match(texts.textContent, /Lift out of service/);
  assert.match(texts.textContent, /18000/);
  assert.equal(
    card.shadowRoot.querySelector(".notice").getAttribute("aria-expanded"),
    "true"
  );

  // Clicking again folds it back.
  card.shadowRoot.querySelector(".notice").click();
  assert.equal(card.shadowRoot.querySelector(".notice-texts"), null);
});

test("expand_alerts unfolds the alert text from the start", () => {
  const card = mount({ expand_alerts: true }, withAlerts(makeHass()));

  assert.ok(card.shadowRoot.querySelector(".notice-texts"));
  // And it can still be folded away by hand.
  card.shadowRoot.querySelector(".notice").click();
  assert.equal(card.shadowRoot.querySelector(".notice-texts"), null);
});

test("escapes the alert text, which comes straight from Renfe", () => {
  const hass = withAlerts(makeHass());
  hass.states[ATOCHA_ALERTS].attributes.alerts = [
    { scope: "line", kind: "warning", target: "C7", text: '<img src=x onerror="alert(1)">' },
  ];
  const card = mount({ expand_alerts: true }, hass);

  assert.equal(card.shadowRoot.querySelectorAll("img").length, 0);
  assert.match(card.shadowRoot.querySelector(".notice-texts").textContent, /<img/);
});

test("explains the ambiguous numbers with tooltips", () => {
  const card = mount();
  const rows = routeRows(card);
  const t = (node, sel) => node.querySelector(sel).getAttribute("title");

  // A bare "+4 min" next to a wait is easy to misread, so it says what it is.
  assert.match(t(rows[0], ".delay"), /late/);
  assert.equal(t(rows[0], ".platform"), "Platform");
  assert.match(t(rows[0], ".wait"), /Departs at \d{1,2}[:.]\d{2}/);
  assert.equal(t(card.shadowRoot, ".station-code"), "Station code");
  // The accessibility glyph is labelled for screen readers.
  assert.equal(
    rows[1].querySelector(".destination svg title").textContent,
    "Accessible train"
  );
});

test("labels an early train as ahead of schedule, not as a delay", () => {
  const hass = makeHass();
  hass.states[ATOCHA].attributes.departures = [
    { line: "C3", line_colour: C3, destination: CHAMARTIN, destination_code: "17000", minutes: 30, delay: -3, platform: "5", accessible: true, time: "2026-08-05T16:53:00+02:00" },
  ];
  const card = mount({}, hass);
  const row = routeRows(card)[0];

  assert.equal(row.querySelector(".delay"), null);
  assert.equal(row.querySelector(".early").textContent, "-3 min");
  assert.match(row.querySelector(".early").getAttribute("title"), /ahead of schedule/);
});

test("translates to Spanish when Home Assistant is in Spanish", () => {
  const hass = makeHass({ language: "es" });
  const card = mount({}, hass);

  assert.equal(text(card.shadowRoot, ".title"), "Renfe - Salidas");
  assert.match(text(card.shadowRoot, ".polled"), /^Actualizado hace/);
  assert.equal(text(card.shadowRoot, ".interval"), "cada 120 s");
  assert.equal(
    routeRows(card)[0].querySelector(".platform").getAttribute("title"),
    "Vía"
  );
});

test("escapes station names so a hostile payload cannot inject markup", () => {
  const hass = makeHass();
  hass.states[ATOCHA].attributes.station_name = '<img src=x onerror="alert(1)">';
  const card = mount({}, hass);

  // No element may come from the payload; the card renders no images at all.
  assert.equal(card.shadowRoot.querySelectorAll("img").length, 0);
  assert.match(text(card.shadowRoot, ".station .name"), /<img/);
});

test("rejects a malformed entities option", () => {
  const card = new RenfeCard();
  assert.throws(
    () => card.setConfig({ entities: "not-a-list" }),
    /must be a list/
  );
});

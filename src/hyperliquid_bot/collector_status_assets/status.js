"use strict";

const labels = {
  unknown: "Onbekend",
  fresh: "Data actueel",
  capturing: "Data verzamelen",
  starting: "Opstarten",
  publishing: "Naar NAS schrijven",
  completed: "Meting afgerond",
  failed: "Meting gestopt met fout",
  stale: "Status verouderd",
  degraded: "Controle nodig",
  recovering: "Herverbinden",
  outage: "Onderbreking",
  synchronized: "Gesynchroniseerd",
};
const venueNames = {
  hyperliquid: "Hyperliquid",
  binance: "Binance",
  kraken: "Kraken",
  "bitvavo-standard": "Bitvavo",
};
const descriptions = {
  hyperliquid: "BTC perpetual · publieke marktdata",
  binance: "BTC spot + USD-M · publieke marktdata",
  kraken: "BTC/USD · publieke marktdata",
  "bitvavo-standard": "BTC/EUR · Standard publieke marktdata",
};
const formatNumber = new Intl.NumberFormat("nl-NL");
const formatTime = new Intl.DateTimeFormat("nl-NL", {
  timeZone: "Europe/Amsterdam",
  dateStyle: "short",
  timeStyle: "medium",
});
const byId = (id) => document.getElementById(id);
const text = (id, value) => {
  byId(id).textContent = value;
};
function element(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
}
function badge(state) {
  return element(
    "span",
    "pill " + (labels[state] ? state : "unknown"),
    labels[state] || labels.unknown,
  );
}
function bytes(value) {
  if (!Number.isFinite(value) || value < 0) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return (
    new Intl.NumberFormat("nl-NL", { maximumFractionDigits: index ? 1 : 0 }).format(value) +
    " " +
    units[index]
  );
}
function age(value) {
  if (!Number.isFinite(value)) return "onbekend";
  if (value < 60)
    return new Intl.NumberFormat("nl-NL", { maximumFractionDigits: 1 }).format(value) + " s";
  return Math.floor(value / 60) + " min " + Math.floor(value % 60) + " s";
}
function timestamp(value) {
  const date = value ? new Date(value) : null;
  return date && Number.isFinite(date.valueOf()) ? formatTime.format(date) : "onbekend";
}
function count(value) {
  return Number.isSafeInteger(value) && value >= 0 ? formatNumber.format(value) : "—";
}
function showVenue(lane) {
  const card = element("article", "venue");
  const heading = element("div", "venue-heading");
  const title = element("div", "venue-title");
  title.append(element("span", "venue-letter", (venueNames[lane.lane] || "?").slice(0, 1)));
  const name = element("div");
  name.append(
    element("h3", "", venueNames[lane.lane] || "Onbekende bron"),
    element("small", "", descriptions[lane.lane] || ""),
  );
  title.append(name);
  heading.append(title, badge(lane.state));
  card.append(heading);
  if (!lane.feeds.length)
    card.append(element("p", "muted", "Nog geen herkenbare gegevens over de datakanalen."));
  for (const feed of lane.feeds) {
    const row = element("div", "feed");
    const feedName = element("span", "feed-name", feed.name);
    feedName.title = "Laatste ontvangst: " + timestamp(feed.last_market_utc);
    row.append(
      feedName,
      element("span", "feed-time", age(feed.age_seconds) + " geleden"),
      badge(feed.state),
    );
    card.append(row);
  }
  const counters = element("div", "venue-counters");
  for (const [label, value] of [
    ["Geschreven delen", lane.writer_published_parts],
    ["Records in buffer", lane.writer_pending_records],
  ]) {
    const item = element("span", "", label + " ");
    item.append(element("strong", "", count(value)));
    counters.append(item);
  }
  card.append(counters);
  const work = lane.publication?.work?.state === "seal_present";
  const archive = lane.publication?.archive?.state === "seal_present";
  card.append(
    element(
      "div",
      "seals",
      "Publicatiemarker: werk " +
        (work ? "aanwezig" : "onbevestigd") +
        " · archief " +
        (archive ? "aanwezig" : "onbevestigd"),
    ),
  );
  return card;
}
function showStorage(storage) {
  const target = byId("storage");
  target.replaceChildren();
  for (const [key, label] of [
    ["spool", "Tijdelijke buffer"],
    ["work", "Actieve werksets"],
    ["archive", "Gesloten archieven"],
  ]) {
    const disk = storage[key] || {};
    const card = element("article", "disk");
    const heading = element("div", "disk-heading");
    heading.append(
      element("span", "", label),
      element("span", "", disk.state === "available" ? "BESCHIKBAAR" : "ONBEKEND"),
    );
    card.append(
      heading,
      element("div", "disk-free", bytes(disk.available_bytes) + " vrij"),
      element(
        "div",
        "disk-meta",
        "Van " + bytes(disk.total_bytes) + " zichtbaar op dit bestandssysteem",
      ),
    );
    const bar = element("div", "bar");
    const fill = element("div", "bar-fill");
    if (
      Number.isFinite(disk.total_bytes) &&
      disk.total_bytes > 0 &&
      Number.isFinite(disk.available_bytes)
    )
      fill.style.width =
        Math.max(0, Math.min(100, (1 - disk.available_bytes / disk.total_bytes) * 100)) + "%";
    bar.append(fill);
    card.append(bar);
    target.append(card);
  }
}
function render(data) {
  text("connection", "Pagina verbonden");
  byId("connection").className = "connection online";
  const state = labels[data.overall] ? data.overall : "unknown";
  const overall = byId("overall");
  overall.textContent = labels[state];
  overall.className = "pill " + state;
  text("updated", "Gecontroleerd " + timestamp(data.generated_utc));
  const controller = data.controller;
  text("controller-phase", controller ? labels[controller.phase] : "Onbekend");
  text(
    "heartbeat",
    controller
      ? "Laatste status: " + age(controller.age_seconds) + " geleden"
      : "Geen bevestigde meetperiode",
  );
  text("source-count", controller ? count(data.lanes.length) : "—");
  text(
    "chunk",
    controller && controller.chunk_index !== null
      ? "Meetdeel " +
          (controller.chunk_index + 1) +
          (controller.chunk_count ? " van " + controller.chunk_count : "")
      : "Meetperiode onbekend",
  );
  text("spool-used", bytes(controller?.spool_allocated_bytes));
  text("clock-state", data.clock.state === "synchronized" ? "In orde" : "Onbekend");
  text(
    "clock-detail",
    data.clock.state === "synchronized"
      ? "Afwijking " + data.clock.offset_ms + " ms · meting " + age(data.clock.age_seconds)
      : "Geen recente geldige klokmeting",
  );
  const notice = byId("notice");
  notice.className = "notice";
  const messages = {
    capturing:
      "De collector rapporteert recente status en alle vereiste datakanalen ontvangen actuele marktdata.",
    starting: "De collector start op. Ontvangst van marktdata is nog niet bevestigd.",
    publishing:
      "Dit meetdeel is gestopt met verzamelen. De bestanden worden gecontroleerd en naar de NAS geschreven.",
    completed:
      "Deze begrensde meting is afgerond. De statuspagina blijft bereikbaar; er wordt voor deze meting geen nieuwe data verzameld.",
    failed:
      "De meting rapporteert een fout. Bewaarde data zijn niet automatisch geschikt voor onderzoek.",
    stale:
      "De collectorstatus is verouderd. De statuspagina kan niet bevestigen dat de meting nog correct loopt.",
    degraded:
      "De collector rapporteert activiteit, maar niet alle datakanalen zijn aantoonbaar actueel. Bekijk de bronnen hieronder.",
    unknown:
      "De pagina is bereikbaar, maar kan geen geldige status van de dataverzameling vaststellen.",
  };
  notice.textContent = messages[state];
  if (["failed", "stale"].includes(state)) notice.classList.add("error");
  else if (["degraded", "unknown", "starting"].includes(state)) notice.classList.add("warning");
  const venues = byId("venues");
  venues.classList.remove("disconnected");
  venues.replaceChildren();
  if (!data.lanes.length)
    venues.append(
      element(
        "div",
        "empty",
        "Geen bevestigde actieve bronnen. Er worden geen ontbrekende gegevens ingevuld.",
      ),
    );
  for (const lane of data.lanes) venues.append(showVenue(lane));
  text(
    "inactive-sources",
    data.inactive_lanes?.length
      ? "Niet ingeschakeld in deze meting: " +
          data.inactive_lanes.map((lane) => venueNames[lane]).join(", ")
      : "",
  );
  showStorage(data.storage);
  text("campaign", controller?.campaign || "Onbekend");
  text(
    "profile",
    controller?.storage_profile === "truenas-zfs"
      ? "TrueNAS App · directe datasetopslag"
      : controller?.storage_profile === "vm-nfs"
        ? "Ubuntu-VM · NAS via NFS"
        : "Onbekend",
  );
  text("source", controller?.source_commit || "Onbekend");
  text("image", controller?.image_digest || "Onbekend");
}
async function poll() {
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), 4500);
  try {
    const response = await fetch("/api/status", { cache: "no-store", signal: abort.signal });
    if (!response.ok) throw new Error("Status unavailable");
    const data = await response.json();
    if (data.schema !== "collector-operator-status-v1") throw new Error("Unknown status schema");
    render(data);
  } catch {
    text("connection", "Verbinding verbroken");
    byId("connection").className = "connection";
    text("overall", "Status onbekend");
    byId("overall").className = "pill unknown";
    text(
      "notice",
      "De status kon niet worden vernieuwd. Onderstaande gegevens kunnen verouderd zijn; actuele dataontvangst is niet bevestigd.",
    );
    byId("notice").className = "notice error";
    text("controller-phase", "Niet bevestigd");
    text("heartbeat", "Geen actuele verbinding met de statusservice");
    text("clock-state", "Onbekend");
    text("clock-detail", "Geen actuele klokmeting bevestigd");
    byId("venues").classList.add("disconnected");
    for (const item of byId("venues").querySelectorAll(".pill")) {
      item.className = "pill unknown";
      item.textContent = "Niet bevestigd";
    }
    byId("storage").replaceChildren(
      element("div", "empty", "Geen actuele opslagmeting bevestigd."),
    );
  } finally {
    clearTimeout(timer);
    setTimeout(poll, 5000);
  }
}
poll();

(function () {
  "use strict";

  const startPointSelect = document.getElementById("start-point-select");
  const modeSelect = document.getElementById("mode-select");
  const statusMessage = document.getElementById("status-message");
  const legendList = document.getElementById("legend-list");
  const panelTitle = document.getElementById("panel-title");
  const coverageNote = document.getElementById("coverage-note");
  const departureNote = document.getElementById("departure-note");

  const FILL_OPACITY = 0.6;

  const map = L.map("map", { zoomControl: true, attributionControl: true });

  // Place-name labels render in their own pane above the isochrone fill
  // (overlayPane, z=400) but below markers (markerPane, z=600), so labels
  // stay legible through the colour fill without covering the start marker.
  map.createPane("labels");
  map.getPane("labels").style.zIndex = 500;
  map.getPane("labels").style.pointerEvents = "none";

  const ATTRIBUTION =
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';

  let baseLayer = null;
  let labelLayer = null;

  function prefersDark() {
    const stamped = document.documentElement.getAttribute("data-theme");
    if (stamped === "dark") return true;
    if (stamped === "light") return false;
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  // The basemap follows the viewer's theme so the chrome around the data
  // doesn't glare in a dark room. Split into a labels-free base and a
  // transparent labels-only layer so place names sit above the colour fill.
  function applyBasemap() {
    const variant = prefersDark() ? "dark" : "light";
    if (baseLayer) map.removeLayer(baseLayer);
    if (labelLayer) map.removeLayer(labelLayer);

    baseLayer = L.tileLayer(
      `https://{s}.basemaps.cartocdn.com/${variant}_nolabels/{z}/{x}/{y}{r}.png`,
      { attribution: ATTRIBUTION, maxZoom: 19, subdomains: "abcd" }
    ).addTo(map);

    labelLayer = L.tileLayer(
      `https://{s}.basemaps.cartocdn.com/${variant}_only_labels/{z}/{x}/{y}{r}.png`,
      { pane: "labels", maxZoom: 19, subdomains: "abcd" }
    ).addTo(map);
  }

  applyBasemap();
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyBasemap);

  let config = null;
  let isochroneLayer = null;
  let startMarker = null;
  let boundaryLayer = null;
  let requestToken = 0;

  function setStatus(message, kind) {
    statusMessage.textContent = message || "";
    statusMessage.classList.toggle("is-visible", Boolean(message));
    statusMessage.classList.toggle("is-loading", kind === "loading");
    statusMessage.classList.toggle("is-error", kind === "error");
  }

  function populateSelect(selectEl, items, labelKey) {
    const fragment = document.createDocumentFragment();
    for (const item of items) {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item[labelKey];
      fragment.appendChild(option);
    }
    selectEl.replaceChildren(fragment);
  }

  function bandsFastToSlow(cfg) {
    return [...cfg.time_bands].sort((a, b) => b.order - a.order);
  }

  function buildLegend(cfg) {
    const fragment = document.createDocumentFragment();
    for (const band of bandsFastToSlow(cfg)) {
      const row = document.createElement("li");
      row.className = "legend__row";

      const swatch = document.createElement("span");
      swatch.className = "legend__swatch";
      swatch.style.setProperty("--band", band.color);

      const label = document.createElement("span");
      label.textContent = band.label;

      row.append(swatch, label);
      fragment.appendChild(row);
    }
    legendList.replaceChildren(fragment);
  }

  function describeDeparture(cfg) {
    const raw = cfg.boundary && cfg.boundary.departure_time;
    if (!raw) return "";
    const when = new Date(raw);
    if (Number.isNaN(when.getTime())) return "";
    const day = when.toLocaleDateString("en-GB", { weekday: "long", timeZone: "UTC" });
    const time = when.toLocaleTimeString("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "UTC",
    });
    return `Journey times for a ${day} ${time} departure.`;
  }

  function styleFeature(feature) {
    const band = config.time_bands.find((b) => b.id === feature.properties.band_id);
    const color = band ? band.color : "#999999";
    return {
      fillColor: color,
      fillOpacity: FILL_OPACITY,
      color: color,
      weight: 1,
      opacity: 0.85,
    };
  }

  function onEachBand(feature, layer) {
    const band = config.time_bands.find((b) => b.id === feature.properties.band_id);
    if (!band) return;
    layer.bindTooltip(band.label, {
      sticky: true,
      className: "band-tooltip",
      direction: "top",
    });
  }

  function updateStartMarker(startPoint) {
    if (startMarker) map.removeLayer(startMarker);
    startMarker = L.marker([startPoint.lat, startPoint.lng], {
      icon: L.divIcon({ className: "start-marker", iconSize: [16, 16] }),
      keyboard: false,
    })
      .addTo(map)
      .bindTooltip(startPoint.name, { className: "place-tooltip", direction: "top", offset: [0, -6] });
  }

  // Fit the boundary into whatever space the panel leaves free. The panel sits
  // to the left on wide screens and across the top on narrow ones, so the
  // padding is measured rather than hardcoded -- and clamped, because padding
  // wider than the map itself makes Leaflet zoom out to the whole country.
  function fitToBoundary() {
    if (!boundaryLayer) return;
    const gap = 20;
    const mapRect = map.getContainer().getBoundingClientRect();
    const panelRect = document.getElementById("panel").getBoundingClientRect();
    const maxX = mapRect.width * 0.42;
    const maxY = mapRect.height * 0.42;

    const panelSpansWidth = panelRect.width >= mapRect.width - 4;
    const padTopLeft = panelSpansWidth
      ? [gap, Math.min(panelRect.bottom + gap, maxY)]
      : [Math.min(panelRect.right + gap, maxX), gap];

    map.fitBounds(boundaryLayer.getBounds(), {
      paddingTopLeft: padTopLeft,
      paddingBottomRight: [gap, gap],
    });
  }

  async function loadIsochrones(startId, modeId) {
    const token = ++requestToken;
    const url = `data/isochrones/${startId}__${modeId}.geojson`;
    setStatus("Loading journey times…", "loading");

    let response;
    try {
      response = await fetch(url);
    } catch (err) {
      if (token === requestToken) setStatus("Couldn't reach the travel time data. Check your connection and try again.", "error");
      return;
    }

    if (token !== requestToken) return; // a newer selection superseded this one

    if (!response.ok) {
      setStatus("No travel time data for this combination yet. Run scripts/generate_isochrones.py to add it.", "error");
      if (isochroneLayer) {
        map.removeLayer(isochroneLayer);
        isochroneLayer = null;
      }
      return;
    }

    const geojson = await response.json();
    if (token !== requestToken) return;

    if (isochroneLayer) map.removeLayer(isochroneLayer);
    isochroneLayer = L.geoJSON(geojson, {
      style: styleFeature,
      onEachFeature: onEachBand,
    }).addTo(map);

    if (boundaryLayer) boundaryLayer.bringToFront();
    setStatus("", null);
  }

  function onSelectionChange() {
    const startId = startPointSelect.value;
    const modeId = modeSelect.value;
    const startPoint = config.start_points.find((sp) => sp.id === startId);
    panelTitle.textContent = startPoint.name;
    updateStartMarker(startPoint);
    loadIsochrones(startId, modeId);
  }

  async function init() {
    const configResponse = await fetch("config.json");
    config = await configResponse.json();

    populateSelect(startPointSelect, config.start_points, "name");
    populateSelect(modeSelect, config.transport_modes, "label");
    buildLegend(config);

    if (config.boundary && config.boundary.buffer_miles) {
      coverageNote.textContent = `Tyne and Wear plus ${config.boundary.buffer_miles} miles, land only.`;
    }
    departureNote.textContent = describeDeparture(config);

    const boundaryResponse = await fetch("data/boundary.geojson");
    if (boundaryResponse.ok) {
      const boundaryGeojson = await boundaryResponse.json();
      boundaryLayer = L.geoJSON(boundaryGeojson, {
        style: {
          fill: false,
          color: getComputedStyle(document.documentElement).getPropertyValue("--boundary-stroke").trim() || "#4a5458",
          weight: 1.25,
          dashArray: "3 4",
          opacity: 0.7,
          interactive: false,
        },
      }).addTo(map);
      fitToBoundary();
    } else {
      map.setView([54.97, -1.6], 10);
    }

    startPointSelect.addEventListener("change", onSelectionChange);
    modeSelect.addEventListener("change", onSelectionChange);

    onSelectionChange();
  }

  init().catch(() => {
    setStatus("Couldn't load the map configuration.", "error");
  });
})();

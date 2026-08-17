(function () {
  "use strict";

  const startPointSelect = document.getElementById("start-point-select");
  const modeSelect = document.getElementById("mode-select");
  const statusMessage = document.getElementById("status-message");

  const map = L.map("map", { zoomControl: true });

  L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    maxZoom: 19,
    subdomains: "abcd",
  }).addTo(map);

  let config = null;
  let isochroneLayer = null;
  let startMarker = null;

  function setStatus(message, isError) {
    statusMessage.textContent = message || "";
    statusMessage.classList.toggle("error", Boolean(isError));
  }

  function populateSelect(selectEl, items, labelKey) {
    selectEl.innerHTML = "";
    for (const item of items) {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item[labelKey];
      selectEl.appendChild(option);
    }
  }

  function buildLegend(cfg) {
    const legendControl = L.control({ position: "bottomleft" });
    legendControl.onAdd = function () {
      const container = L.DomUtil.create("div", "legend");
      const bandsFastToSlow = [...cfg.time_bands].sort((a, b) => b.order - a.order);
      const rows = bandsFastToSlow
        .map(
          (band) =>
            `<div class="legend-row"><span class="legend-swatch" style="background:${band.color}"></span><span>${band.label}</span></div>`
        )
        .join("");
      container.innerHTML = `<h2>Travel time</h2>${rows}<div class="legend-note">Shows travel time within Tyne and Wear + a 5 mile buffer.</div>`;
      L.DomEvent.disableClickPropagation(container);
      return container;
    };
    legendControl.addTo(map);
  }

  function styleFeature(feature) {
    const band = config.time_bands.find((b) => b.id === feature.properties.band_id);
    const color = band ? band.color : "#999999";
    return {
      fillColor: color,
      fillOpacity: 0.6,
      color: color,
      weight: 1,
      opacity: 0.8,
    };
  }

  function updateStartMarker(startPoint) {
    if (startMarker) {
      map.removeLayer(startMarker);
    }
    startMarker = L.marker([startPoint.lat, startPoint.lng], {
      icon: L.divIcon({ className: "start-marker-icon", iconSize: [14, 14] }),
    })
      .addTo(map)
      .bindTooltip(startPoint.name, { permanent: false });
  }

  async function loadIsochrones(startId, modeId) {
    const url = `data/isochrones/${startId}__${modeId}.geojson`;
    setStatus("Loading…", false);

    if (isochroneLayer) {
      map.removeLayer(isochroneLayer);
      isochroneLayer = null;
    }

    let response;
    try {
      response = await fetch(url);
    } catch (err) {
      setStatus("Could not load travel time data (network error).", true);
      return;
    }

    if (!response.ok) {
      setStatus(
        "No precomputed data for this combination yet. Run scripts/generate_isochrones.py to generate it.",
        true
      );
      return;
    }

    const geojson = await response.json();
    isochroneLayer = L.geoJSON(geojson, { style: styleFeature }).addTo(map);
    setStatus("", false);
  }

  function onSelectionChange() {
    const startId = startPointSelect.value;
    const modeId = modeSelect.value;
    const startPoint = config.start_points.find((sp) => sp.id === startId);
    updateStartMarker(startPoint);
    loadIsochrones(startId, modeId);
  }

  async function init() {
    const configResponse = await fetch("config.json");
    config = await configResponse.json();

    populateSelect(startPointSelect, config.start_points, "name");
    populateSelect(modeSelect, config.transport_modes, "label");
    buildLegend(config);

    const boundaryResponse = await fetch("data/boundary.geojson");
    if (boundaryResponse.ok) {
      const boundaryGeojson = await boundaryResponse.json();
      const boundaryLayer = L.geoJSON(boundaryGeojson, {
        style: { fill: false, color: "#52514e", weight: 1.5, dashArray: "4 4", opacity: 0.6 },
      }).addTo(map);
      map.fitBounds(boundaryLayer.getBounds(), { padding: [16, 16] });
    } else {
      map.setView([54.97, -1.6], 11);
    }

    startPointSelect.addEventListener("change", onSelectionChange);
    modeSelect.addEventListener("change", onSelectionChange);

    onSelectionChange();
  }

  init();
})();

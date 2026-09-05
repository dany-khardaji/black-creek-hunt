const usesSeparateLocalServer =
  location.protocol === "file:" ||
  (location.port !== "" && location.port !== "8000");
const API_URL =
  location.protocol === "file:"
    ? "http://localhost:8000"
    : usesSeparateLocalServer
      ? `${location.protocol}//${location.hostname}:8000`
      : "";

const map = L.map("map", { zoomControl: true }).setView([35.645, -78.442], 15);
map.zoomControl.setPosition("bottomleft");
L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  { attribution: "Imagery &copy; Esri", maxZoom: 19 },
).addTo(map);
map.attributionControl.setPrefix(false);

// Leaflet caches the container size at init, so it must be told when the
// viewport changes: rotation, and mobile browser chrome collapsing or expanding.
let resizeFrame = 0;
function resizeMapToViewport() {
  // Toolbar animations fire a burst of resizes; settle on the last one.
  cancelAnimationFrame(resizeFrame);
  resizeFrame = requestAnimationFrame(() => {
    map.invalidateSize({ animate: false, pan: false });
  });
}

if (window.ResizeObserver) {
  new ResizeObserver(resizeMapToViewport).observe(
    document.getElementById("map"),
  );
} else {
  window.addEventListener("resize", resizeMapToViewport);
  window.addEventListener("orientationchange", resizeMapToViewport);
}

// visualViewport tracks toolbar collapse that never fires a window resize.
window.visualViewport?.addEventListener("resize", resizeMapToViewport);

// Covers the bfcache restore path, where no resize fires at all.
window.addEventListener("pageshow", resizeMapToViewport);

// Tooltips are hover-driven, so on touch they never fire and the panel carries
// the same information in a readable form. Skip binding them entirely there.
const hasHover = window.matchMedia("(hover: hover)").matches;
const isBottomSheet = () => window.matchMedia("(max-width: 640px)").matches;

const standMarkers = new Map();
const featureMarkers = new Map();
const drafts = new Map();

let mapState = { stands: [], map_features: [], live_count: 0 };
let selectedStandId = new URLSearchParams(location.search).get("stand");
let isInitialLoad = true;
let isRefreshing = false;

const panel = document.getElementById("panel");
const panelName = document.getElementById("panel-name");
const panelStatus = document.getElementById("panel-status");
const panelMessage = document.getElementById("panel-message");
const checkInForm = document.getElementById("check-in-form");
const guestFields = document.getElementById("guest-fields");
const addGuestButton = document.getElementById("add-guest");
const checkOutButton = document.getElementById("check-out-button");
const appMessage = document.getElementById("app-message");
const liveCounter = document.getElementById("live-counter");
const liveCountValue = document.getElementById("live-count-value");
const liveCountLabel = document.getElementById("live-count-label");

class ApiError extends Error {
  constructor(status, payload) {
    super(`Request failed with status ${status}`);
    this.status = status;
    this.payload = payload;
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(
    /[&<>'"]/g,
    (character) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "'": "&#39;",
        '"': "&quot;",
      })[character],
  );
}

function statusColor(status) {
  if (status === "active") return "var(--active)";
  if (status === "overdue") return "var(--overdue)";
  return "var(--open)";
}

function formatCheckedInTime(value) {
  if (!value) return "";

  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/New_York",
    timeZoneName: "short",
  }).format(new Date(value));
}

function announce(message, tone = "info") {
  appMessage.textContent = message;
  appMessage.dataset.tone = tone;
  appMessage.hidden = message === "";
}

async function requestJson(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body) headers.set("Content-Type", "application/json");

  const response = await fetch(`${API_URL}${path}`, {
    credentials: "include",
    ...options,
    headers,
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    // Some successful responses may not have a JSON body.
  }

  if (!response.ok) throw new ApiError(response.status, payload);
  return payload;
}

function formatApiError(error) {
  if (!(error instanceof ApiError)) {
    return "The network request failed. Check that the API is running and try again.";
  }

  const detail = error.payload?.detail;
  if (detail?.message) return detail.message;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => item.msg)
      .filter(Boolean)
      .join(" ");
  }
  if (typeof detail === "string") return detail;
  return `The request failed (${error.status}). Please try again.`;
}

function emptyDraft() {
  return { guests: [], message: "", tone: "info" };
}

function getDraft(standId) {
  if (!drafts.has(standId)) drafts.set(standId, emptyDraft());
  return drafts.get(standId);
}

function renderPanelMessage(draft = null) {
  const messageState = draft || { message: "", tone: "info" };
  panelMessage.textContent = messageState.message;
  panelMessage.dataset.tone = messageState.tone;
}

function captureDraft({ clearMessage = false } = {}) {
  if (!selectedStandId || checkInForm.hidden) return;

  const draft = getDraft(selectedStandId);
  draft.guests = [...guestFields.querySelectorAll(".guest-row")].map((row) => ({
    name: row.querySelector('[data-field="name"]').value,
    phone: row.querySelector('[data-field="phone"]').value,
    stand_id: row.querySelector('[data-field="stand_id"]').value,
  }));

  if (clearMessage) {
    draft.message = "";
    draft.tone = "info";
    renderPanelMessage(draft);
  }
}

function availableSeatsForStand(stand) {
  const capacity = stand.capacity ?? 1;
  const occupiedCount =
    stand.occupied_count ?? (stand.status === "open" ? 0 : capacity);
  return stand.available_seats ?? Math.max(capacity - occupiedCount, 0);
}

function seatsReservedByDraft(draft, standId, excludedGuestIndex) {
  return (
    (standId === selectedStandId ? 1 : 0) +
    draft.guests.filter(
      (guest, index) =>
        index !== excludedGuestIndex && guest.stand_id === standId,
    ).length
  );
}

function guestStandOptions(draft, guestIndex) {
  const guest = draft.guests[guestIndex];

  return mapState.stands
    .filter((stand) => {
      const reservedSeats = seatsReservedByDraft(
        draft,
        stand.id,
        guestIndex,
      );
      const seatsRemaining =
        availableSeatsForStand(stand) - reservedSeats;

      return seatsRemaining > 0 || stand.id === guest.stand_id;
    })
    .map((stand) => {
      const reservedSeats = seatsReservedByDraft(
        draft,
        stand.id,
        guestIndex,
      );
      const seatsRemaining = Math.max(
        availableSeatsForStand(stand) - reservedSeats,
        0,
      );
      const selected = stand.id === guest.stand_id ? " selected" : "";
      const seatWord = seatsRemaining === 1 ? "seat" : "seats";
      const label = seatsRemaining
        ? `${stand.name} (${seatsRemaining} ${seatWord} available)`
        : `${stand.name} (now unavailable)`;
      return `<option value="${escapeHtml(stand.id)}"${selected}>${escapeHtml(label)}</option>`;
    })
    .join("");
}

function renderGuestRows() {
  if (!selectedStandId) return;

  const draft = getDraft(selectedStandId);
  guestFields.innerHTML = draft.guests
    .map(
      (guest, index) => `
        <section class="guest-row" data-guest-index="${index}">
          <div class="guest-heading">
            <h3>Guest ${index + 1}</h3>
            <button class="remove-guest" type="button" data-remove-guest="${index}">
              Remove
            </button>
          </div>
          <label for="guest-${index}-name">
            Name
            <input id="guest-${index}-name" data-field="name" type="text"
              value="${escapeHtml(guest.name)}" autocomplete="name" required />
          </label>
          <label for="guest-${index}-phone">
            Phone number
            <input id="guest-${index}-phone" data-field="phone" type="tel"
              value="${escapeHtml(guest.phone)}" autocomplete="tel" required />
          </label>
          <label for="guest-${index}-stand">
            Stand
            <select id="guest-${index}-stand" data-field="stand_id" required>
              <option value="">Choose an open stand</option>
              ${guestStandOptions(draft, index)}
            </select>
          </label>
        </section>
      `,
    )
    .join("");

  addGuestButton.hidden = draft.guests.length >= 2;
}

function renderPanel() {
  if (!selectedStandId) return;

  const stand = mapState.stands.find(
    (candidate) => candidate.id === selectedStandId,
  );
  if (!stand) {
    closePanel({ updateUrl: true });
    return;
  }

  panelName.textContent = stand.name;
  const statusText = stand.status[0].toUpperCase() + stand.status.slice(1);
  const capacity = stand.capacity ?? 1;
  const occupiedCount = stand.occupied_count ?? (stand.occupied_by ? 1 : 0);
  const availableSeats = availableSeatsForStand(stand);
  const occupants = stand.occupants ?? [];
  const seatWord = capacity === 1 ? "seat" : "seats";
  const occupantList = occupants.length
    ? `<ul class="occupant-list">${occupants
        .map((occupant) => {
          const role =
            occupant.occupant_type === "guest" ? "Guest" : "Member";
          const guestOf = occupant.guest_of
            ? ` · with ${escapeHtml(occupant.guest_of)}`
            : "";
          return `<li><strong>${escapeHtml(occupant.display_name)}</strong><span>${role}${guestOf} · ${escapeHtml(formatCheckedInTime(occupant.checked_in_at))}</span></li>`;
        })
        .join("")}</ul>`
    : "<p>Available for check-in</p>";

  panelStatus.innerHTML = `
    <span class="status-label" style="--status-color: ${statusColor(stand.status)}">
      ${escapeHtml(statusText)}
    </span>
    <p class="capacity-summary"><strong>${occupiedCount}/${capacity}</strong> ${seatWord} occupied · ${availableSeats} available</p>
    ${occupantList}
  `;

  const canCheckIn = availableSeats > 0 && !stand.can_check_out;
  checkInForm.hidden = !canCheckIn;
  checkOutButton.hidden = !stand.can_check_out;

  if (canCheckIn) {
    const draft = getDraft(stand.id);
    renderGuestRows();
    renderPanelMessage(draft);
  } else {
    renderPanelMessage();
  }

  panel.classList.add("open");
  panel.setAttribute("aria-hidden", "false");
}

// Slide the selected stand into the strip of map the panel leaves uncovered:
// above the bottom sheet on phones, left of the side panel on desktop.
function revealSelectedStand(standId) {
  const marker = standMarkers.get(standId);
  if (!marker) return;

  const point = map.latLngToContainerPoint(marker.getLatLng());
  const size = map.getSize();
  const panelBox = panel.getBoundingClientRect();
  const target = isBottomSheet()
    ? L.point(size.x / 2, Math.max(size.y - panelBox.height, 0) / 2)
    : L.point(Math.max(size.x - panelBox.width, 0) / 2, size.y / 2);

  const offset = point.subtract(target);
  if (Math.abs(offset.x) < 24 && Math.abs(offset.y) < 24) return;

  map.panBy(offset, { animate: true, duration: 0.35 });
}

function openPanel(standId, { updateUrl = true, focusHeading = true } = {}) {
  captureDraft();
  selectedStandId = standId;
  renderPanel();
  syncStandMarkers();
  revealSelectedStand(standId);

  if (updateUrl) {
    const url = new URL(location.href);
    if (url.searchParams.get("stand") !== standId) {
      url.searchParams.set("stand", standId);
      history.pushState({}, "", url);
    }
  }

  if (focusHeading) panelName.focus({ preventScroll: true });
}

function closePanel({ updateUrl = true } = {}) {
  captureDraft();
  selectedStandId = null;
  panel.classList.remove("open");
  panel.setAttribute("aria-hidden", "true");
  syncStandMarkers();

  if (updateUrl) {
    const url = new URL(location.href);
    url.searchParams.delete("stand");
    history.pushState({}, "", url);
  }
}

const GAUGE_RADIUS = 12.5;
const GAUGE_CIRCUMFERENCE = 2 * Math.PI * GAUGE_RADIUS;

function standMarkerIcon(stand) {
  const capacity = Math.max(Number(stand.capacity) || 1, 1);
  const occupiedCount = Math.min(Number(stand.occupied_count) || 0, capacity);
  const occupants = stand.occupants ?? [];
  const hasGuest = occupants.some(
    (occupant) => occupant?.occupant_type === "guest",
  );
  const isFull = occupiedCount >= capacity;
  const filledLength = (occupiedCount / capacity) * GAUGE_CIRCUMFERENCE;
  const occupancyClass = occupiedCount > 0 ? " occupied" : "";
  const fullClass = isFull ? " full" : "";
  const guestClass = hasGuest ? " has-guest" : "";
  // Re-applied on every refresh because setIcon rebuilds the element.
  const selectedClass = stand.id === selectedStandId ? " selected" : "";
  // A full ring needs no dash pattern; a partial one lights the occupied share
  // and leaves the remainder to the dim track underneath.
  const gaugeDash = isFull
    ? ""
    : `stroke-dasharray="${filledLength.toFixed(2)} ${(GAUGE_CIRCUMFERENCE - filledLength).toFixed(2)}"`;

  return L.divIcon({
    className: "stand-marker-anchor",
    html: `
      <span class="stand-marker ${stand.status}${occupancyClass}${fullClass}${guestClass}${selectedClass}" style="--marker-color: ${statusColor(stand.status)}">
        <svg class="stand-gauge" viewBox="0 0 34 34" aria-hidden="true" focusable="false">
          <circle class="selection-ring" cx="17" cy="17" r="16" />
          <circle class="gauge-track" cx="17" cy="17" r="${GAUGE_RADIUS}" />
          ${
            occupiedCount > 0
              ? `<circle class="gauge-fill" cx="17" cy="17" r="${GAUGE_RADIUS}" ${gaugeDash} transform="rotate(-90 17 17)" />`
              : ""
          }
          <circle class="stand-body" cx="17" cy="17" r="9.4" />
          <g class="stand-glyph">
            <path class="stand-roof" d="M12.4 16.6 17 12.6l4.6 4z" />
            <rect x="13.7" y="17" width="6.6" height="4.6" rx="0.6" />
          </g>
        </svg>
      </span>
    `,
    iconSize: [34, 34],
    iconAnchor: [17, 17],
  });
}

function syncStandMarkers() {
  const currentIds = new Set();

  for (const stand of mapState.stands) {
    currentIds.add(stand.id);
    let marker = standMarkers.get(stand.id);

    if (!marker) {
      marker = L.marker([stand.lat, stand.lng], {
        keyboard: true,
        title: stand.name,
      }).addTo(map);
      marker.on("click", () => openPanel(stand.id));
      if (hasHover) {
        marker.bindTooltip("", { direction: "top", offset: [0, -18] });
      }
      standMarkers.set(stand.id, marker);
    }

    marker.setLatLng([stand.lat, stand.lng]);
    marker.setIcon(standMarkerIcon(stand));
    if (hasHover) {
      marker.setTooltipContent(
        `${escapeHtml(stand.name)}: ${escapeHtml(stand.status)} — ${stand.occupied_count ?? 0}/${stand.capacity ?? 1} seats${stand.occupied_by ? ` — ${escapeHtml(stand.occupied_by)}` : ""}`,
      );
    }
  }

  for (const [standId, marker] of standMarkers) {
    if (!currentIds.has(standId)) {
      map.removeLayer(marker);
      standMarkers.delete(standId);
    }
  }
}

function syncFeatureMarkers() {
  const currentIds = new Set();

  for (const feature of mapState.map_features) {
    currentIds.add(feature.id);
    let marker = featureMarkers.get(feature.id);

    if (!marker) {
      marker = L.circleMarker([feature.lat, feature.lng], {
        radius: 6,
        color: "#f4f1e8",
        weight: 2,
        fillColor: "#121412",
        fillOpacity: 1,
      }).addTo(map);
      marker.bindTooltip(escapeHtml(feature.name));
      featureMarkers.set(feature.id, marker);
    } else {
      marker.setLatLng([feature.lat, feature.lng]);
      marker.setTooltipContent(escapeHtml(feature.name));
    }
  }

  for (const [featureId, marker] of featureMarkers) {
    if (!currentIds.has(featureId)) {
      map.removeLayer(marker);
      featureMarkers.delete(featureId);
    }
  }
}

async function refreshMapState({
  quiet = false,
  captureCurrentDraft = true,
} = {}) {
  if (isRefreshing) return;
  isRefreshing = true;
  if (captureCurrentDraft) captureDraft();

  if (isInitialLoad) announce("Loading map…");

  try {
    const data = await requestJson("/api/map-state");
    mapState = data;

    const noun = data.live_count === 1 ? "Hunter" : "Hunters";
    liveCountValue.textContent = data.live_count;
    liveCountLabel.textContent = noun;
    liveCounter.dataset.active = String(data.live_count > 0);
    liveCounter.setAttribute(
      "aria-label",
      `${data.live_count} ${noun} on the property`,
    );

    const deepLinkedStandId = isInitialLoad ? selectedStandId : null;

    syncStandMarkers();
    syncFeatureMarkers();
    renderPanel();
    // A stand opened from a URL param has no markers to pan to until now.
    if (deepLinkedStandId) revealSelectedStand(deepLinkedStandId);
    if (!quiet) announce("");
  } catch (error) {
    announce(formatApiError(error), "error");
  } finally {
    isInitialLoad = false;
    isRefreshing = false;
  }
}

addGuestButton.addEventListener("click", () => {
  captureDraft({ clearMessage: true });
  const draft = getDraft(selectedStandId);
  if (draft.guests.length >= 2) return;

  draft.guests.push({ name: "", phone: "", stand_id: "" });
  renderGuestRows();
  guestFields
    .querySelector('.guest-row:last-child [data-field="name"]')
    ?.focus();
});

guestFields.addEventListener("input", () => {
  captureDraft({ clearMessage: true });
});

guestFields.addEventListener("change", (event) => {
  captureDraft({ clearMessage: true });
  if (event.target.matches('[data-field="stand_id"]')) {
    renderGuestRows();
  }
});

guestFields.addEventListener("click", (event) => {
  const removeButton = event.target.closest("[data-remove-guest]");
  if (!removeButton) return;

  captureDraft({ clearMessage: true });
  const draft = getDraft(selectedStandId);
  draft.guests.splice(Number(removeButton.dataset.removeGuest), 1);
  renderGuestRows();
});

checkInForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  captureDraft();
  if (!checkInForm.reportValidity()) return;

  const standId = selectedStandId;
  const draft = getDraft(standId);
  const controls = [...checkInForm.elements];
  controls.forEach((control) => (control.disabled = true));
  draft.message = "Checking in…";
  draft.tone = "info";
  renderPanelMessage(draft);

  try {
    await requestJson("/api/hunts", {
      method: "POST",
      body: JSON.stringify({ stand_id: standId, guests: draft.guests }),
    });
    drafts.delete(standId);
    await refreshMapState({ quiet: true, captureCurrentDraft: false });
    announce("Check-in complete.");
  } catch (error) {
    draft.message = formatApiError(error);
    draft.tone = "error";
    await refreshMapState({ quiet: true });
    renderPanelMessage(draft);
  } finally {
    controls.forEach((control) => (control.disabled = false));
  }
});

checkOutButton.addEventListener("click", async () => {
  const stand = mapState.stands.find(
    (candidate) => candidate.id === selectedStandId,
  );
  if (!stand?.can_check_out || !stand.hunt_id) return;

  checkOutButton.disabled = true;
  panelMessage.textContent = "Checking out…";
  panelMessage.dataset.tone = "info";

  try {
    await requestJson(`/api/hunts/${stand.hunt_id}/check-out`, {
      method: "POST",
    });
    await refreshMapState({ quiet: true });
    announce("Checkout complete.");
  } catch (error) {
    const message = formatApiError(error);
    await refreshMapState({ quiet: true });
    panelMessage.textContent = message;
    panelMessage.dataset.tone = "error";
  } finally {
    checkOutButton.disabled = false;
  }
});

document.getElementById("panel-close").addEventListener("click", () => {
  closePanel();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && panel.classList.contains("open")) {
    closePanel();
  }
});

// Tapping bare map dismisses the panel: on a phone the Close button is a reach.
map.on("click", () => {
  if (panel.classList.contains("open")) closePanel();
});

window.addEventListener("popstate", () => {
  const standId = new URLSearchParams(location.search).get("stand");
  if (standId) {
    openPanel(standId, { updateUrl: false, focusHeading: false });
  } else {
    closePanel({ updateUrl: false });
  }
});

refreshMapState();
setInterval(() => refreshMapState(), 30000);

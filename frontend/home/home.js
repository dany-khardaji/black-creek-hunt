// Homepage: the club's properties and how many people are out right now.

const propertyList = document.getElementById("property-list");
const homeMessage = document.getElementById("home-message");
const liveCounter = document.getElementById("live-counter");
const liveCountValue = document.getElementById("live-count-value");
const liveCountLabel = document.getElementById("live-count-label");
const signOutButton = document.getElementById("sign-out");
const overdueBanner = document.getElementById("overdue-banner");
const overdueAnnouncer = document.getElementById("overdue-announcer");

function formatCheckedInTime(value) {
  if (!value) return "";

  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/New_York",
    timeZoneName: "short",
  }).format(new Date(value));
}

// Who was overdue last time. A change here is what triggers the spoken alert,
// so the same people must not retrigger it as their hours climb.
let lastOverdueKey = "";

function overdueLine(entry) {
  return `${entry.name} — ${entry.stand_name}, in since ${formatCheckedInTime(entry.checked_in_at)} (${entry.hours_out}h)`;
}

// Built element by element rather than as text, because a guest name is typed
// by a member and must never be treated as page code.
function renderOverdue(overdue = []) {
  const lines = overdue.map(overdueLine);
  // Keyed on who is overdue, not the rendered text. hours_out climbs every few
  // minutes, and announcing that again would interrupt for no new information.
  const key = overdue.map((entry) => entry.hunt_id).join("|");
  const isSamePeople = key === lastOverdueKey;
  lastOverdueKey = key;

  if (overdue.length === 0) {
    overdueBanner.replaceChildren();
    overdueBanner.hidden = true;
    overdueAnnouncer.textContent = "";
    return;
  }

  // Same people, so only the hours changed: update the text in place. The
  // banner is not a live region, so this is silent.
  if (isSamePeople) {
    const items = overdueBanner.querySelectorAll("li");
    if (items.length === lines.length) {
      items.forEach((item, index) => {
        item.textContent = lines[index];
      });
      return;
    }
  }

  const hunterWord = overdue.length === 1 ? "hunter" : "hunters";
  const heading = document.createElement("strong");

  // The warning sign is its own element so it can be sized and colored apart
  // from the words, matching the property page.
  const warningSign = document.createElement("span");
  warningSign.className = "overdue-sign";
  warningSign.setAttribute("aria-hidden", "true");
  warningSign.textContent = "⚠︎";

  const summaryText = document.createElement("span");
  summaryText.textContent = `Safety check - ${overdue.length} ${hunterWord} overdue`;

  heading.replaceChildren(warningSign, summaryText);

  const list = document.createElement("ul");
  for (const line of lines) {
    const item = document.createElement("li");
    item.textContent = line;
    list.append(item);
  }

  overdueBanner.replaceChildren(heading, list);
  overdueBanner.hidden = false;

  // Spoken only when the people change, never when their hours tick up.
  overdueAnnouncer.textContent = `Safety check: ${overdue.length} ${hunterWord} overdue. ${lines.join(". ")}`;
}

function announce(message, tone = "info") {
  homeMessage.textContent = message;
  homeMessage.dataset.tone = tone;
  homeMessage.hidden = message === "";
}

class ApiError extends Error {
  constructor(status) {
    super(`Request failed with status ${status}`);
    this.status = status;
  }
}

// Set once a redirect is under way, so two failing requests cannot both fire it.
let isRedirecting = false;

// An expired or missing session means the sign-in page, not an error message.
// replace() rather than assign() so the back button does not return here and
// bounce straight back to the login page again.
function redirectToLogin() {
  if (isRedirecting) return;
  isRedirecting = true;
  window.location.replace("/login");
}

async function requestJson(path) {
  const response = await fetch(path, { credentials: "include" });
  if (!response.ok) throw new ApiError(response.status);
  return response.json();
}

function renderProperties(properties) {
  if (properties.length === 0) {
    announce("No properties are available yet.");
    return;
  }

  // Built piece by piece rather than as text, so a property name can never be
  // treated as page code.
  propertyList.replaceChildren(
    ...properties.map((property) => {
      const item = document.createElement("li");
      const card = document.createElement("a");
      card.className = "property-card";
      card.href = `/property/${encodeURIComponent(property.slug)}`;

      const name = document.createElement("h2");
      name.textContent = property.name;
      card.append(name);

      if (property.description) {
        const description = document.createElement("p");
        description.textContent = property.description;
        card.append(description);
      }

      item.append(card);
      return item;
    }),
  );

  announce("");
}

function renderLiveCount(count) {
  const noun = count === 1 ? "Hunter" : "Hunters";
  liveCountValue.textContent = count;
  liveCountLabel.textContent = noun;
  liveCounter.dataset.active = String(count > 0);
  liveCounter.setAttribute("aria-label", `${count} ${noun} across all properties`);
}

async function load() {
  try {
    const [properties, live] = await Promise.all([
      requestJson("/api/properties"),
      requestJson("/api/live-count"),
    ]);
    renderProperties(properties);
    renderLiveCount(live.live_count);
    renderOverdue(live.overdue);
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      redirectToLogin();
      return;
    }
    announce(
      "Could not load properties. Check that the API is running and try again.",
      "error",
    );
  }
}

// Only the count and the overdue alert change while someone sits here, so the
// property cards are left alone rather than rebuilt every half minute.
async function refreshLiveCount() {
  try {
    const live = await requestJson("/api/live-count");
    renderLiveCount(live.live_count);
    renderOverdue(live.overdue);
  } catch (error) {
    // An expired session must not leave a stale overdue alert on screen
    // claiming someone is still out.
    if (error instanceof ApiError && error.status === 401) {
      redirectToLogin();
      return;
    }
    // Any other failure leaves the last known numbers up; the next refresh
    // corrects them, and load() already reported any first-load failure.
  }
}

load();
setInterval(refreshLiveCount, 30000);

signOutButton.addEventListener("click", async () => {
  signOutButton.disabled = true;

  try {
    await fetch("/api/auth/logout", { method: "POST", credentials: "include" });
  } catch {
    // the cookie may already be gone, so a failed request still ends at login
  }

  // replace() rather than assign() so back does not return to a signed-out page
  window.location.replace("/login");
});

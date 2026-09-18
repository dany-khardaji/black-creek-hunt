// Homepage: the club's properties and how many people are out right now.

const propertyList = document.getElementById("property-list");
const homeMessage = document.getElementById("home-message");
const liveCounter = document.getElementById("live-counter");
const liveCountValue = document.getElementById("live-count-value");
const liveCountLabel = document.getElementById("live-count-label");
const signOutButton = document.getElementById("sign-out");

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

load();

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

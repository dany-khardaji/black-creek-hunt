// Homepage: the club's properties and how many people are out right now.

const propertyList = document.getElementById("property-list");
const homeMessage = document.getElementById("home-message");
const liveCounter = document.getElementById("live-counter");
const liveCountValue = document.getElementById("live-count-value");
const liveCountLabel = document.getElementById("live-count-label");

function announce(message, tone = "info") {
  homeMessage.textContent = message;
  homeMessage.dataset.tone = tone;
  homeMessage.hidden = message === "";
}

async function requestJson(path) {
  const response = await fetch(path, { credentials: "include" });
  if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
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
  } catch {
    announce(
      "Could not load properties. Check that the API is running and try again.",
      "error",
    );
  }
}

load();

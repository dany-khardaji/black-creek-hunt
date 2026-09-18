// Sign-in page. Only the email and password form needs code here; the Google
// button is an ordinary link the server handles.

const passwordForm = document.getElementById("password-form");
const loginMessage = document.getElementById("login-message");

// what the server sends back on ?error= when google sign-in does not complete
const GOOGLE_ERRORS = {
  not_a_member: "That Google account is not on the member list.",
  google_failed: "Google sign-in did not complete. Please try again.",
  google_unavailable: "Google sign-in is unavailable. Use your password below.",
};

function announce(message, tone = "info") {
  loginMessage.textContent = message;
  loginMessage.dataset.tone = tone;
}

// an unknown code says nothing rather than echoing the url back at the member
function showGoogleError() {
  const code = new URLSearchParams(window.location.search).get("error");
  if (!code) return;

  if (GOOGLE_ERRORS[code]) announce(GOOGLE_ERRORS[code], "error");

  // drops ?error= so a refresh does not show a stale message
  window.history.replaceState({}, "", window.location.pathname);
}

showGoogleError();

passwordForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!passwordForm.reportValidity()) return;

  const controls = [...passwordForm.elements];
  controls.forEach((control) => (control.disabled = true));
  announce("Signing in…");

  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: document.getElementById("login-email").value,
        password: document.getElementById("login-password").value,
      }),
    });

    if (!response.ok) {
      // Deliberately vague: never reveal whether an email is on the allowlist.
      announce("Those sign-in details were not recognized.", "error");
      return;
    }

    window.location.assign("/");
  } catch {
    announce("The network request failed. Please try again.", "error");
  } finally {
    controls.forEach((control) => (control.disabled = false));
  }
});

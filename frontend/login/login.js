// Sign-in page. Only the email and password form needs code here; the Google
// button is an ordinary link the server handles.

const passwordForm = document.getElementById("password-form");
const loginMessage = document.getElementById("login-message");

function announce(message, tone = "info") {
  loginMessage.textContent = message;
  loginMessage.dataset.tone = tone;
}

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

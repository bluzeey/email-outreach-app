"use strict";

window.addEventListener("load", () => {
    const page = document.getElementById("owner-login");
    const status = document.getElementById("login-status");
    const button = document.getElementById("google-signin");
    if (!page || !status || !button) return;
    if (!window.google?.accounts?.id) {
        status.textContent = "Google sign-in could not load. Reload this page to try again.";
        return;
    }

    let submitting = false;
    window.google.accounts.id.initialize({
        client_id: page.dataset.clientId,
        nonce: page.dataset.nonce,
        auto_select: false,
        ux_mode: "popup",
        callback: async (result) => {
            if (submitting || !result.credential) return;
            submitting = true;
            status.textContent = "Signing in…";
            try {
                const response = await fetch("/login/google", {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({credential: result.credential, next: page.dataset.next}),
                });
                const data = await response.json();
                if (!response.ok) {
                    status.textContent = data.detail || "Sign-in failed. Reload this page to try again.";
                    return;
                }
                // The server returns only a sanitized local path. Check again before navigation.
                const destination = new URL(data.redirect, window.location.origin);
                destination.search = "";
                destination.hash = "";
                window.location.assign(destination.origin === window.location.origin ? destination.href : "/");
            } catch {
                status.textContent = "Sign-in could not finish. Reload this page to try again.";
            } finally {
                // Google credentials stay in this callback; never persist or log them.
                result.credential = "";
                submitting = false;
            }
        },
    });
    window.google.accounts.id.renderButton(button, {
        type: "standard", theme: "outline", size: "large", text: "signin_with", shape: "rectangular",
    });
    status.textContent = "Access is limited to the app owner.";
});

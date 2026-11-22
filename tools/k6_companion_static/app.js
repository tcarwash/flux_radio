/* Flux K6 Companion — live serial only, no simulated radio state. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const keys = new Set(["0","1","2","3","4","5","6","7","8","9","STAR",
    "POUND","MENU","EXIT","UP","DOWN","VM","AB","BAND","SIDE1","SIDE2"]);
  let token = sessionStorage.getItem("flux-k6-token") || "";
  let requiresAuth = false;
  let polling = null;
  let refreshing = false;
  let keyQueue = Promise.resolve();
  let keyPending = 0;
  let pendingRefresh = null;
  let lastScreen = null;
  let screenURL = null;
  let lastOnline = false;
  const buttons = [...document.querySelectorAll("[data-key]")];

  function setActivity(message) { $("activity-text").textContent = message; }
  function screenState(message) { $("screen-state").textContent = message; }
  function clock() { return new Date().toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"}); }
  function describeError(error) { return error instanceof Error ? error.message : String(error); }

  async function request(path, options = {}) {
    const headers = {...(options.headers || {})};
    if (requiresAuth && token) headers["X-K6-Token"] = token;
    const resp = await fetch(path, {...options, headers, cache: "no-store"});
    if (!resp.ok) {
      let message = "HTTP " + resp.status;
      try { const data = await resp.json(); message = data.error || message; }
      catch (_) { /* use HTTP error */ }
      const err = new Error(message);
      err.status = resp.status;
      throw err;
    }
    return resp;
  }

  function showAuth(message = "") {
    $("auth-overlay").hidden = false;
    $("auth-error").textContent = message;
    $("auth-token").focus();
    setActivity("Access token required");
    stopPolling();
  }
  function hideAuth() { $("auth-overlay").hidden = true; $("auth-error").textContent = ""; }

  function setConnection(connected, message = "") {
    lastOnline = connected;
    $("status-dot").classList.toggle("online", connected);
    $("status-dot").classList.toggle("offline", !connected);
    $("link-title").textContent = connected ? "Radio online" : "Radio unavailable";
    $("link-detail").textContent = connected ? "Live serial link is responding" : (message || "Check the radio and cable");
    if (!connected) screenState(lastScreen ? "FRAME STALE" : "NO SIGNAL");
    if (!connected && !lastScreen) $("lcd-message").textContent = "RADIO UNAVAILABLE";
  }

  function formatMHz(hz) {
    if (!Number.isFinite(hz)) return "—";
    return (hz / 1_000_000).toFixed(5);
  }
  function updateStatus(data) {
    setConnection(true);
    $("battery").textContent = Number(data.battery_volts).toFixed(2);
    $("rssi").textContent = Number.isFinite(data.rssi_dbm) ? String(data.rssi_dbm) : "—";
    const a = data.vfos?.[0] || {}, b = data.vfos?.[1] || {};
    $("vfo-a").textContent = formatMHz(a.hz);
    $("vfo-b").textContent = formatMHz(b.hz);
    $("vfo-a-mode").textContent = (a.mode || "—") + (data.master === 0 ? " • RX" : "");
    $("vfo-b-mode").textContent = (b.mode || "—") + (data.master === 1 ? " • RX" : "");
    $("last-refresh").textContent = clock();
    if (data.transmitting) {
      setActivity("Radio is transmitting; virtual keys may be refused.");
    } else if (data.sleeping) {
      setActivity("Radio is sleeping; virtual keys may be refused.");
    } else if (!keyPending) {
      setActivity("Radio ready · live link active");
    }
  }

  async function getStatus() {
    const response = await request("/api/status");
    const data = await response.json();
    updateStatus(data);
    return data;
  }

  async function getScreen() {
    const response = await request("/api/screen");
    const blob = await response.blob();
    if (blob.type !== "image/png" || blob.size < 50) throw new Error("Invalid LCD image response.");
    const url = URL.createObjectURL(blob);
    const image = $("lcd-image");
    const oldURL = screenURL;
    image.onload = () => { if (oldURL) URL.revokeObjectURL(oldURL); };
    image.src = url;
    screenURL = url;
    lastScreen = blob;
    $("lcd-glass").classList.add("has-image");
    screenState("SYNCED " + clock());
    return blob;
  }

  async function refresh() {
    if (refreshing || keyPending || (requiresAuth && !token)) return;
    refreshing = true;
    screenState("READING LCD");
    try {
      // The server serializes access to one physical programming cable.
      const [status, screen] = await Promise.allSettled([getStatus(), getScreen()]);
      if (status.status === "rejected") {
        const err = status.reason;
        if (err?.status === 401) return showAuth("Session expired; enter your access token.");
        setConnection(false, describeError(err));
        setActivity(describeError(err));
      }
      if (screen.status === "rejected") {
        const err = screen.reason;
        if (err?.status === 401) return showAuth("Session expired; enter your access token.");
        screenState("LCD READ FAILED");
        setActivity("LCD: " + describeError(err));
        if (!lastScreen) $("lcd-message").textContent = "LCD UNAVAILABLE";
      }
    } catch (err) {
      setConnection(false, describeError(err));
    } finally {
      refreshing = false;
    }
  }

  function stopPolling() {
    if (polling) clearInterval(polling);
    polling = null;
  }
  function startPolling() {
    stopPolling();
    refresh();
    polling = setInterval(refresh, 2800);
  }

  function flashButton(button) {
    if (!button) return;
    button.classList.add("pressed");
    setTimeout(() => button.classList.remove("pressed"), 160);
  }

  function sendKey(key, long = false, button = null) {
    if (!keys.has(key)) return;
    if (long && /^[1-9]$/.test(key)) {
      setActivity("K6 firmware does not support holding numeric keys 1–9.");
      $("long-mode").checked = false;
      return;
    }
    if (requiresAuth && !token) return showAuth();
    $("long-mode").checked = false;
    if (pendingRefresh) clearTimeout(pendingRefresh);
    keyPending++;
    flashButton(button || document.querySelector('[data-key="' + key + '"]'));
    keyQueue = keyQueue.catch(() => {}).then(async () => {
      const verb = long ? "Holding" : "Pressing";
      setActivity(verb + " " + key + "...");
      try {
        const resp = await request("/api/key", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({key, long})
        });
        const result = await resp.json();
        if (!result.queued) throw new Error("Radio did not acknowledge key event.");
        setActivity((long ? "Long " : "") + key + " sent");
      } catch (err) {
        if (err.status === 401) showAuth("Access token required.");
        setActivity("Key " + key + ": " + describeError(err));
      } finally {
        keyPending--;
        if (keyPending === 0) pendingRefresh = setTimeout(refresh, 230);
      }
    });
  }

  // Touch/mouse hold -> one logical LONG event upon release. No synthetic PTT
  // or keydown/key-up train is ever sent to the device.
  buttons.forEach((button) => {
    let downAt = 0;
    let suppressClick = false;
    const key = button.dataset.key;
    button.addEventListener("contextmenu", (event) => event.preventDefault());
    button.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 && event.pointerType === "mouse") return;
      downAt = performance.now();
      button.classList.add("pressed");
    });
    button.addEventListener("pointerup", () => {
      button.classList.remove("pressed");
      if (downAt && performance.now() - downAt >= 600) {
        suppressClick = true;
        sendKey(key, true, button);
      }
      downAt = 0;
    });
    button.addEventListener("pointercancel", () => {
      downAt = 0; button.classList.remove("pressed");
    });
    button.addEventListener("pointerleave", () => button.classList.remove("pressed"));
    button.addEventListener("click", (event) => {
      if (suppressClick) { suppressClick = false; return; }
      sendKey(key, !!(event.shiftKey || $("long-mode").checked), button);
    });
  });

  const map = {
    ArrowUp: "UP", ArrowDown: "DOWN", Enter: "MENU", Escape: "EXIT",
    "*": "STAR", "#": "POUND", a: "AB", A: "AB", v: "VM", V: "VM",
    b: "BAND", B: "BAND", "[": "SIDE1", "]": "SIDE2",
  };
  document.addEventListener("keydown", (event) => {
    if (event.repeat || event.ctrlKey || event.altKey || event.metaKey) return;
    if (event.target.closest("input,textarea,select,[contenteditable]")) return;
    if ((event.key === "Enter" || event.key === " ") && event.target.closest("button")) return;
    const key = /^[0-9]$/.test(event.key) ? event.key : map[event.key];
    if (key) {
      event.preventDefault();
      sendKey(key, event.shiftKey || $("long-mode").checked);
    } else if (event.key.toLowerCase() === "r") {
      event.preventDefault();
      refresh();
    }
  });

  $("refresh-btn").addEventListener("click", refresh);
  $("save-btn").addEventListener("click", async () => {
    try {
      if (!lastScreen) await getScreen();
      if (!lastScreen) return;
      const fileURL = URL.createObjectURL(lastScreen);
      const a = document.createElement("a");
      a.href = fileURL;
      a.download = "flux-k6-" + new Date().toISOString().replaceAll(":", "-") + ".png";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(fileURL), 1000);
      setActivity("Saved native 128×64 LCD capture.");
    } catch (err) { setActivity("Save failed: " + describeError(err)); }
  });

  $("auth-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const proposed = $("auth-token").value;
    token = proposed;
    try {
      await getStatus();
      sessionStorage.setItem("flux-k6-token", proposed);
      hideAuth();
      startPolling();
    } catch (err) {
      if (err.status === 401) {
        token = "";
        $("auth-error").textContent = "Incorrect access token.";
      } else {
        sessionStorage.setItem("flux-k6-token", proposed);
        hideAuth();
        setConnection(false, describeError(err));
        startPolling();
      }
    }
  });

  async function init() {
    try {
      const resp = await fetch("/api/config", {cache:"no-store"});
      if (!resp.ok) throw new Error("Companion config unavailable");
      const config = await resp.json();
      $("device-path").textContent = config.device || "—";
      requiresAuth = !!config.auth_required;
      if (requiresAuth && !token) return showAuth();
      startPolling();
    } catch (err) {
      setConnection(false, describeError(err));
      setActivity("Cannot reach companion server.");
    }
  }
  init();
})();

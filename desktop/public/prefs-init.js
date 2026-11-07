// Before anything reads localStorage: put kell's saved settings back (app/api/prefs), and send every later change of a kell key to that
// file. The app's server takes a new port on each start, so the window's own storage starts empty every time (2026-11-07).
(function () {
  var KEY = /^kell[.:]/
  try {
    var x = new XMLHttpRequest()
    x.open("GET", "/api/prefs", false)                       // synchronous on purpose: the page and theme-init.js read these next
    x.send()
    if (x.status === 200) {
      var saved = JSON.parse(x.responseText) || {}
      for (var k in saved) if (KEY.test(k) && typeof saved[k] === "string") localStorage.setItem(k, saved[k])
    }
  } catch (e) { /* no saved settings: the defaults stand */ }
  try {
    var proto = Storage.prototype, set = proto.setItem, remove = proto.removeItem
    var send = function (key, value) {
      try { fetch("/api/prefs", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ key: key, value: value }), keepalive: true }) } catch (e) { /* kept for this run only */ }
    }
    proto.setItem = function (k, v) { set.call(this, k, v); if (this === localStorage && KEY.test(String(k))) send(String(k), String(v)) }
    proto.removeItem = function (k) { remove.call(this, k); if (this === localStorage && KEY.test(String(k))) send(String(k), null) }
  } catch (e) { /* storage blocked */ }
})()

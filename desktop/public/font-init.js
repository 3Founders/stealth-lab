// Before anything paints: put the chosen typeface on <html data-font>, so a saved Inter or Poppins does not flash the
// default face on every start. The choice itself is saved by lib/font.ts and mirrored to desktop-prefs.json by
// prefs-init.js, which runs just before this file.
(function () {
  try {
    var f = localStorage.getItem("kell.font")
    if (f === "inter" || f === "poppins") document.documentElement.dataset.font = f
  } catch (e) { /* storage blocked: the default face stands */ }
})()

// The Services UI is a desktop-app page (desktop/plugin.js). The web dashboard
// only needs this plugin's backend API, and the manifest hides its tab. This
// stub registers an empty component so the dashboard doesn't report a missing
// bundle for a plugin that has no web UI.
(function () {
  if (window.__HERMES_PLUGINS__ && typeof window.__HERMES_PLUGINS__.register === "function") {
    window.__HERMES_PLUGINS__.register("tailnet-services", function TailnetServicesStub() {
      return null;
    });
  }
})();

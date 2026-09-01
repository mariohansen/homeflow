/* User-visible text.
   The interface is German for now; strings live in one table so an English
   locale can be added without touching any view code. */

const de = {
  "app.name": "HomeFlow",

  "connect.lead": "Verbinde dich mit deinem Zuhause-Gateway.",
  "connect.tokenLabel": "Zugangsschlüssel",
  "connect.submit": "Verbinden",
  "connect.hint":
    "Der Schlüssel steht in der .env deines Gateways unter HOMEFLOW_DEV_CLIENT_TOKEN. Er wird nur auf diesem Gerät gespeichert.",
  "connect.checking": "Verbinde …",

  "tab.home": "Zuhause",
  "tab.activity": "Aktivität",
  "tab.settings": "Einstellungen",

  "title.home": "Zuhause",
  "title.activity": "Aktivität",
  "title.settings": "Einstellungen",

  "link.live": "Live",
  "link.connecting": "Verbinde",
  "link.offline": "Getrennt",

  "home.empty": "Noch keine Geräte. Das Gateway hat nichts gefunden.",
  "home.noRoom": "Weitere Geräte",
  "activity.empty": "Noch keine Aktivität aufgezeichnet.",

  "settings.connection": "Verbindung",
  "settings.client": "Client",
  "settings.mode": "Betrieb",
  "settings.host": "Gateway",
  "settings.devices": "Geräte",
  "settings.security": "Sicherheit",
  "settings.securityNote":
    "Aktionen mit hohem Risiko – Tür entriegeln oder öffnen – werden vom Gateway abgelehnt, bis die frische Geräte-Freigabe per Face ID implementiert ist. Das ist Absicht, kein Fehler.",
  "settings.signOut": "Zugang von diesem Gerät entfernen",

  "mode.demo": "Demo-Modus",
  "mode.live": "Produktiv",

  "badge.offline": "Offline",
  "badge.stale": "Veraltet",
  "badge.unknown": "Status unbekannt",

  "device.power": "Ein",
  "device.brightness": "Helligkeit",
  "device.volume": "Lautstärke",
  "device.playback": "Wiedergabe",
  "device.play": "Abspielen",
  "device.pause": "Pause",
  "device.observed": "Stand: {time}",

  "state.on": "An",
  "state.off": "Aus",
  "device.readOnly":
    "Nur Anzeige. Die Steuerung wird erst freigegeben, wenn sie am Ger\u00e4t gepr\u00fcft wurde.",

  "pool.current": "Wassertemperatur",
  "pool.target": "Ziel {value} °C",
  "pool.targetLabel": "Zieltemperatur",
  "pool.heater": "Heizung",
  "pool.filter": "Filterpumpe",
  "pool.bubbles": "Massagedüsen",
  "pool.panelLock": "Bedienfeldsperre",

  "lock.LOCKED": "Abgeschlossen",
  "lock.UNLOCKED": "Entriegelt",
  "lock.UNKNOWN": "Unbekannt",
  "lock.lock": "Abschließen",
  "lock.unlock": "Entriegeln",

  "program.IDLE": "Bereit",
  "program.RUNNING": "Läuft",
  "program.FINISHED": "Fertig",
  "program.UNKNOWN": "Unbekannt",
  "program.remaining": "Noch {value}",

  "playback.PLAYING": "Spielt",
  "playback.PAUSED": "Pausiert",
  "playback.STOPPED": "Gestoppt",
  "playback.UNKNOWN": "Unbekannt",

  "command.unknown":
    "Das Gerät hat nicht rechtzeitig geantwortet. Ob der Befehl ausgeführt wurde, ist unklar.",
  "command.failed": "Befehl fehlgeschlagen.",

  "error.unauthenticated": "Zugang abgelehnt. Bitte neu verbinden.",
  "error.forbidden": "Diese Aktion ist nicht erlaubt.",
  "error.action_authorization_required":
    "Diese Aktion braucht eine frische Freigabe auf dem Gerät. Noch nicht verfügbar.",
  "error.device_not_found": "Gerät nicht gefunden.",
  "error.command_not_found": "Befehl nicht gefunden.",
  "error.capability_not_supported": "Das Gerät kann das nicht.",
  "error.invalid_parameters": "Ungültige Eingabe.",
  "error.parameter_out_of_range": "Wert außerhalb des zulässigen Bereichs.",
  "error.device_unavailable": "Gerät ist offline.",
  "error.rate_limited": "Zu viele Anfragen. Kurz warten.",
  "error.internal_error": "Unerwarteter Fehler im Gateway.",
  "error.network": "Gateway nicht erreichbar.",

  "activity.command.requested": "Befehl angefordert",
  "activity.command.completed": "Befehl abgeschlossen",
  "activity.command.denied": "Befehl abgelehnt",

  "time.hoursMinutes": "{hours} Std {minutes} Min",
  "time.minutes": "{minutes} Min",
};

const catalogue = { de };
const locale = "de";

/** Look up a string and interpolate {placeholders}. */
export function t(key, vars) {
  const table = catalogue[locale];
  let value = Object.hasOwn(table, key) ? table[key] : key;
  if (vars) {
    for (const [name, replacement] of Object.entries(vars)) {
      value = value.replaceAll(`{${name}}`, String(replacement));
    }
  }
  return value;
}

/** Fill every element carrying a data-i18n attribute. */
export function applyStaticStrings(root = document) {
  for (const node of root.querySelectorAll("[data-i18n]")) {
    node.textContent = t(node.dataset.i18n);
  }
}

/**
 * Whether two IANA zone names are the same zone. Names differ for one zone: Gen9 stores the
 * canonical one (Asia/Kolkata), while browsers report ICU's (Asia/Calcutta). Both go through
 * ICU, which gives one name for both; an unknown name is only the same as itself.
 */
export function sameZone(a: string, b: string): boolean {
  if (a === b) return true;
  try {
    const icu = (zone: string) => new Intl.DateTimeFormat("en-US", { timeZone: zone }).resolvedOptions().timeZone;
    return icu(a) === icu(b);
  } catch {
    return false;
  }
}

// The zones whose names differ between CLDR, which browsers report (Chrome 153 says Asia/Calcutta,
// also in Intl.supportedValuesOf), and IANA, which gen9-agent stores (tasks.zone, following
// tzdata's links): every CLDR entry whose `iana` attribute names another zone, CLDR 48's
// common/bcp47/timezone.xml (manual-e2e.md, P3-D8). Any other name a browser reports is IANA's.
const IANA: Record<string, string> = {
  "Africa/Asmera": "Africa/Asmara",
  "America/Buenos_Aires": "America/Argentina/Buenos_Aires",
  "America/Catamarca": "America/Argentina/Catamarca",
  "America/Coral_Harbour": "America/Atikokan",
  "America/Cordoba": "America/Argentina/Cordoba",
  "America/Godthab": "America/Nuuk",
  "America/Indianapolis": "America/Indiana/Indianapolis",
  "America/Jujuy": "America/Argentina/Jujuy",
  "America/Louisville": "America/Kentucky/Louisville",
  "America/Mendoza": "America/Argentina/Mendoza",
  "Asia/Calcutta": "Asia/Kolkata",
  "Asia/Katmandu": "Asia/Kathmandu",
  "Asia/Rangoon": "Asia/Yangon",
  "Asia/Saigon": "Asia/Ho_Chi_Minh",
  "Atlantic/Faeroe": "Atlantic/Faroe",
  "Europe/Kiev": "Europe/Kyiv",
  "Pacific/Enderbury": "Pacific/Kanton",
  "Pacific/Ponape": "Pacific/Pohnpei",
  "Pacific/Truk": "Pacific/Chuuk",
};

/** A zone by IANA's name, as Gen9 saves it: "Asia/Kolkata" for the browser's "Asia/Calcutta". */
export function ianaName(zone: string): string {
  return IANA[zone] ?? zone;
}

/** Where the browser is, by IANA's name. */
export function browserZone(): string {
  return ianaName(Intl.DateTimeFormat().resolvedOptions().timeZone);
}

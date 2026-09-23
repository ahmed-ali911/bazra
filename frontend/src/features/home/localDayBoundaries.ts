// Computes the two instants Home's aggregation endpoint needs, entirely in
// the browser's LOCAL timezone — never derived server-side. setDate/getDate
// operate on local wall-clock calendar fields (year/month/day), so adding
// "1 day" or "7 days" this way correctly lands on the right local midnight
// even across a DST transition, where a local day is 23 or 25 hours long,
// not 24. Using tomorrowStart.getTime() + 24*60*60*1000 instead would be
// wrong on exactly those two days a year — this is why the boundaries are
// computed here, not as a server-side timedelta(days=1)/(days=7) addition
// (see backend home/service.py's docstring for the other half of this).
export function localDayBoundaries(referenceDate: Date = new Date()) {
  const todayStart = new Date(referenceDate);
  todayStart.setHours(0, 0, 0, 0);

  const tomorrowStart = new Date(todayStart);
  tomorrowStart.setDate(tomorrowStart.getDate() + 1);

  const windowEnd = new Date(tomorrowStart);
  windowEnd.setDate(windowEnd.getDate() + 7);

  return {
    tomorrowStart: tomorrowStart.toISOString(),
    windowEnd: windowEnd.toISOString(),
  };
}

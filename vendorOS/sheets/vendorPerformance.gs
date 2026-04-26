// vendorOS — Phase 3: Lead Time Verification
// Copy into NNL_INVENTORY Google Sheets Script Editor

function generateVendorPerformance() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const poSheet = ss.getSheetByName("PO");
  const vendorsSheet = ss.getSheetByName("VENDORS");

  if (!poSheet || !vendorsSheet) {
    SpreadsheetApp.getUi().alert("Error: PO or VENDORS sheet missing.");
    return;
  }

  ss.toast("Analysing lead times from PO data...", "vendorOS", 3);

  const poData = poSheet.getDataRange().getValues();
  const vendorsData = vendorsSheet.getDataRange().getValues();
  const DAY_MS = 24 * 60 * 60 * 1000;
  const now = new Date();
  const cutoff90 = new Date(now - 90 * DAY_MS);
  const cutoff45 = new Date(now - 45 * DAY_MS);

  // Build vendor master: vendor no → {name, statedLeadTime, onTime, avgDelay}
  const vendorMaster = {};
  for (let i = 1; i < vendorsData.length; i++) {
    const vNo = String(vendorsData[i][0]).trim();
    if (!vNo) continue;
    vendorMaster[vNo] = {
      name: vendorsData[i][1],
      statedLeadTime: Number(vendorsData[i][10]) || 0,
      onTimePct: vendorsData[i][7],
      mrpAvgDelay: vendorsData[i][8]
    };
  }

  // Analyse PO data: per vendor, collect actual lead times (90-day window)
  const vendorPOs = {}; // vNo → { all: [], recent: [], older: [] }

  for (let i = 1; i < poData.length; i++) {
    const vendorNo = String(poData[i][36]).trim(); // AG
    const orderDate = poData[i][26];               // AA = Order Date
    const arrivalDate = poData[i][24];             // Y = Arrival Date
    const status = String(poData[i][19]).trim();   // T = Status

    if (!vendorNo || !orderDate || !arrivalDate) continue;
    if (status !== "Delivered" && status !== "Closed" && status !== "Partially delivered") continue;

    const orderD = orderDate instanceof Date ? orderDate : new Date(orderDate);
    const arrivalD = arrivalDate instanceof Date ? arrivalDate : new Date(arrivalDate);

    if (isNaN(orderD) || isNaN(arrivalD)) continue;
    if (orderD < cutoff90) continue; // only last 90 days

    const actualLeadTime = Math.round((arrivalD - orderD) / DAY_MS);
    if (actualLeadTime < 0 || actualLeadTime > 180) continue; // sanity check

    if (!vendorPOs[vendorNo]) vendorPOs[vendorNo] = { all: [], recent: [], older: [] };
    vendorPOs[vendorNo].all.push(actualLeadTime);
    if (orderD >= cutoff45) {
      vendorPOs[vendorNo].recent.push(actualLeadTime); // 0-45 days
    } else {
      vendorPOs[vendorNo].older.push(actualLeadTime);  // 45-90 days
    }
  }

  // Build output
  const headers = [
    "Vendor No", "Vendor Name",
    "Stated Lead Time (days)", "Actual Lead Time 90D avg", "Delta (actual - stated)",
    "POs Analysed", "On Time % (MRP Easy)", "MRP Avg Delay",
    "Trend (0-45d vs 45-90d)", "Recommended LT Update",
    "Action Required"
  ];

  const output = [];

  for (const vNo in vendorMaster) {
    const v = vendorMaster[vNo];
    const pos = vendorPOs[vNo];

    if (!pos || pos.all.length < 1) {
      // Include vendors with no recent POs (they might be low-volume but still active)
      if (v.statedLeadTime > 0) {
        output.push([
          vNo, v.name,
          v.statedLeadTime, "No data", "N/A",
          0, v.onTimePct, v.mrpAvgDelay,
          "No data", "", ""
        ]);
      }
      continue;
    }

    const avg = arr => arr.length ? (arr.reduce((a, b) => a + b, 0) / arr.length).toFixed(1) : null;
    const actualAvg = parseFloat(avg(pos.all));
    const delta = actualAvg - v.statedLeadTime;

    let trend = "Stable";
    const recentAvg = avg(pos.recent);
    const olderAvg = avg(pos.older);
    if (recentAvg && olderAvg) {
      const diff = parseFloat(recentAvg) - parseFloat(olderAvg);
      if (diff > 2) trend = "Worsening";
      else if (diff < -2) trend = "Improving";
    }

    const recommended = delta > 3
      ? `Update MRP Easy to ${Math.ceil(actualAvg)} days`
      : "";

    const actionRequired = delta > 3
      ? `⚠️ Consistently ${delta.toFixed(0)}d late — update lead time in MRP Easy`
      : delta < -3
        ? "✅ Delivering faster than stated"
        : "";

    output.push([
      vNo, v.name,
      v.statedLeadTime, actualAvg, delta.toFixed(1),
      pos.all.length, v.onTimePct, v.mrpAvgDelay,
      trend, recommended, actionRequired
    ]);
  }

  // Sort: biggest positive delta (worst offenders) first
  output.sort((a, b) => {
    const aD = parseFloat(a[4]) || -999;
    const bD = parseFloat(b[4]) || -999;
    return bD - aD;
  });

  let perfSheet = ss.getSheetByName("VENDOR_PERFORMANCE");
  if (!perfSheet) perfSheet = ss.insertSheet("VENDOR_PERFORMANCE");
  perfSheet.clear();
  perfSheet.getRange(1, 1, 1, headers.length).setValues([headers]).setFontWeight("bold");
  if (output.length > 0) {
    perfSheet.getRange(2, 1, output.length, headers.length).setValues(output);
  }

  // Highlight rows where delta > 3 (red) or < -3 (green)
  for (let i = 0; i < output.length; i++) {
    const delta = parseFloat(output[i][4]);
    if (!isNaN(delta)) {
      const bg = delta > 3 ? "#fce8e6" : delta < -3 ? "#e6f4ea" : "#ffffff";
      perfSheet.getRange(i + 2, 1, 1, headers.length).setBackground(bg);
    }
  }

  perfSheet.setFrozenRows(1);
  perfSheet.showSheet();

  const lateCount = output.filter(r => parseFloat(r[4]) > 3).length;
  ss.toast(`Done. ${lateCount} vendors have understated lead times by >3 days — review VENDOR_PERFORMANCE.`, "Lead Time Analysis", 8);
}

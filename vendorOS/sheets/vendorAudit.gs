// vendorOS — Phase 0: Vendor Audit & Cleanup
// Copy into NNL_INVENTORY Google Sheets Script Editor
// Menu: add to onOpen() → ui.createMenu('vendorOS')...

const VENDOR_CATEGORIES = {
  // Add vendor number → category mappings here as you categorise them
  // Example: "V001": "raw-fragrance"
};

const CATEGORY_LIST = [
  "raw-fragrance",
  "raw-wax",
  "raw-wick",
  "raw-dye",
  "raw-ingredient",
  "packaging-glass",
  "packaging-closure",
  "packaging-carton",
  "labels",
  "third-party-finished",
  "sundry",
  "services",
  "uncategorised"
];

function generateVendorAudit() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const vendorsSheet = ss.getSheetByName("VENDORS");
  const itemsSheet = ss.getSheetByName("ITEMS");
  const poSheet = ss.getSheetByName("PO");

  if (!vendorsSheet || !itemsSheet || !poSheet) {
    SpreadsheetApp.getUi().alert("Error: VENDORS, ITEMS, or PO sheet missing. Run MRP Sync first.");
    return;
  }

  ss.toast("Building vendor audit...", "vendorOS", 3);

  const vendorsData = vendorsSheet.getDataRange().getValues();
  const itemsData = itemsSheet.getDataRange().getValues();
  const poData = poSheet.getDataRange().getValues();

  // Build: last PO date per vendor
  const lastPODate = {};
  for (let i = 1; i < poData.length; i++) {
    const vendorNo = String(poData[i][36]).trim(); // AG = Vendor No
    const orderDate = poData[i][26];               // AA = Order Date
    if (vendorNo && orderDate) {
      const d = orderDate instanceof Date ? orderDate : new Date(orderDate);
      if (!lastPODate[vendorNo] || d > lastPODate[vendorNo]) {
        lastPODate[vendorNo] = d;
      }
    }
  }

  // Build: active item count per vendor
  const activeItemCount = {};
  for (let i = 1; i < itemsData.length; i++) {
    const vendorNo = String(itemsData[i][24]).trim(); // Y = Vendor No
    if (vendorNo) {
      activeItemCount[vendorNo] = (activeItemCount[vendorNo] || 0) + 1;
    }
  }

  // Build output rows
  const headers = [
    "Vendor No", "Vendor Name", "Email", "Phone", "URL",
    "Category", "Last PO Date", "Active Items",
    "On Time %", "Avg Delay (days)", "Default Lead Time",
    "Status", "Notes"
  ];

  const output = [];
  const cutoffDate = new Date();
  cutoffDate.setFullYear(cutoffDate.getFullYear() - 1); // 12 months ago

  for (let i = 1; i < vendorsData.length; i++) {
    const vendorNo = String(vendorsData[i][0]).trim();
    if (!vendorNo) continue;

    const name = vendorsData[i][1];
    const phone = vendorsData[i][2];
    const email = vendorsData[i][4];
    const url = vendorsData[i][5];
    const onTime = vendorsData[i][7];
    const avgDelay = vendorsData[i][8];
    const leadTime = vendorsData[i][10];

    const lastDate = lastPODate[vendorNo] || null;
    const itemCount = activeItemCount[vendorNo] || 0;
    const category = VENDOR_CATEGORIES[vendorNo] || "uncategorised";

    let status = "Active";
    if (!lastDate && itemCount === 0) {
      status = "Purge";
    } else if (lastDate && lastDate < cutoffDate && itemCount === 0) {
      status = "Inactive";
    }

    const lastDateStr = lastDate
      ? Utilities.formatDate(lastDate, Session.getScriptTimeZone(), "dd/MM/yyyy")
      : "No POs found";

    output.push([
      vendorNo, name, email, phone, url,
      category, lastDateStr, itemCount,
      onTime, avgDelay, leadTime,
      status, ""
    ]);
  }

  // Sort: Purge first, then Inactive, then Active
  const statusOrder = { "Purge": 0, "Inactive": 1, "Active": 2, "uncategorised": 3 };
  output.sort((a, b) => (statusOrder[a[11]] || 3) - (statusOrder[b[11]] || 3));

  // Write to VENDOR_AUDIT sheet
  let auditSheet = ss.getSheetByName("VENDOR_AUDIT");
  if (!auditSheet) auditSheet = ss.insertSheet("VENDOR_AUDIT");
  auditSheet.clear();
  auditSheet.getRange(1, 1, 1, headers.length).setValues([headers]).setFontWeight("bold");
  if (output.length > 0) {
    auditSheet.getRange(2, 1, output.length, headers.length).setValues(output);
  }

  // Colour code by status
  for (let i = 0; i < output.length; i++) {
    const row = i + 2;
    const status = output[i][11];
    let bg = "#ffffff";
    if (status === "Purge") bg = "#fce8e6";
    else if (status === "Inactive") bg = "#fff8e1";
    auditSheet.getRange(row, 1, 1, headers.length).setBackground(bg);
  }

  auditSheet.setFrozenRows(1);
  auditSheet.showSheet();

  const purgeCount = output.filter(r => r[11] === "Purge").length;
  const inactiveCount = output.filter(r => r[11] === "Inactive").length;
  const uncatCount = output.filter(r => r[5] === "uncategorised").length;

  ss.toast(
    `Done. ${purgeCount} to purge, ${inactiveCount} inactive, ${uncatCount} uncategorised. Review VENDOR_AUDIT tab.`,
    "vendorOS Audit Complete", 8
  );
}

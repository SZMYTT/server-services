// vendorOS — Phase 6: Disposal Item Tracking
// Copy into NNL_INVENTORY Google Sheets Script Editor

const DISPOSAL_ITEMS_SEED = [
  // [Item Name, Category, Unit, Check Freq, Reorder Trigger, Reorder Qty, Avg Weekly Usage]
  ["A4 Printer Paper - White 80gsm", "Office", "Reams", "Weekly", 3, 10, 2],
  ["A4 Printer Paper - Colour (card/labels)", "Office", "Sheets/Box", "Fortnightly", 50, 200, 0],
  ["Ballpoint Pens - Black", "Office", "Items", "Monthly", 5, 20, 0],
  ["Sticky Notes", "Office", "Pads", "Monthly", 2, 10, 0],
  ["Sellotape / Clear Tape (desk)", "Office", "Rolls", "Monthly", 2, 6, 0],
  ["Staples", "Office", "Boxes", "Monthly", 1, 3, 0],
  ["Dymo / Zebra Label Rolls", "Printing", "Rolls", "Weekly", 2, 10, 1],
  ["Printer Toner / Ink Cartridges", "Printing", "Cartridges", "Monthly", 1, 2, 0],
  ["Packing Tape - Brown", "Packaging", "Rolls", "Weekly", 5, 20, 3],
  ["Tissue Paper - White", "Packaging", "Sheets/Ream", "Weekly", 200, 1000, 100],
  ["Bubble Wrap", "Packaging", "Metres/Roll", "Fortnightly", 10, 50, 5],
  ["Void Fill / Packaging Paper", "Packaging", "Rolls", "Weekly", 1, 4, 1],
  ["Small Cardboard Boxes (e.g. 20x20x20)", "Packaging", "Items", "Fortnightly", 20, 100, 0],
  ["Medium Cardboard Boxes (e.g. 40x30x30)", "Packaging", "Items", "Fortnightly", 10, 50, 0],
  ["Surface Cleaner Spray", "Cleaning", "Bottles", "Monthly", 2, 6, 0],
  ["Floor Cleaner", "Cleaning", "Bottles", "Monthly", 1, 3, 0],
  ["Bin Bags - Small (office)", "Cleaning", "Boxes", "Monthly", 1, 2, 0],
  ["Bin Bags - Large (warehouse)", "Cleaning", "Boxes", "Monthly", 1, 3, 0],
  ["Paper Towels / Roll", "Cleaning", "Rolls", "Fortnightly", 3, 12, 1],
  ["Hand Soap", "Cleaning", "Bottles", "Monthly", 2, 6, 0],
  ["Washing-Up Liquid", "Kitchen", "Bottles", "Monthly", 1, 3, 0],
  ["Coffee / Tea / Milk", "Kitchen", "Units", "Weekly", 1, 3, 0.5],
  ["Latex Gloves", "Warehouse", "Boxes", "Monthly", 1, 3, 0],
  ["Permanent Markers", "Warehouse", "Items", "Monthly", 3, 10, 0],
  ["Box Cutters / Stanley Knives", "Warehouse", "Items", "Monthly", 1, 3, 0],
  ["Cable Ties (various)", "Warehouse", "Bags", "Monthly", 1, 3, 0],
];

function seedDisposalsTab() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName("DISPOSALS");
  if (sheet) {
    const ui = SpreadsheetApp.getUi();
    const result = ui.alert("DISPOSALS tab already exists. Overwrite?", ui.ButtonSet.YES_NO);
    if (result !== ui.Button.YES) return;
    sheet.clear();
  } else {
    sheet = ss.insertSheet("DISPOSALS");
  }

  const headers = [
    "Item Name", "Category", "Current Stock", "Unit",
    "Last Checked", "Check Frequency", "Reorder Trigger", "Reorder Qty",
    "Avg Weekly Usage", "Weeks of Stock", "Next Check Due", "Overdue?",
    "Primary Supplier", "Primary URL", "Primary Price",
    "Backup Supplier", "Backup URL", "Backup Price",
    "In Bulk?", "Bulk Saving %", "Notes"
  ];

  sheet.getRange(1, 1, 1, headers.length).setValues([headers]).setFontWeight("bold");

  const today = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "dd/MM/yyyy");

  const rows = DISPOSAL_ITEMS_SEED.map(item => [
    item[0], item[1], "", item[2],    // Name, Category, Stock (blank), Unit
    today, item[3], item[4], item[5], // Last Checked, Freq, Trigger, Reorder Qty
    item[6],                          // Avg Weekly Usage
    "", "", "",                       // Weeks of Stock (formula), Next Check Due (formula), Overdue (formula)
    "", "", "",                       // Primary Supplier, URL, Price
    "", "", "",                       // Backup Supplier, URL, Price
    "N", "", ""                       // In Bulk, Bulk Saving, Notes
  ]);

  sheet.getRange(2, 1, rows.length, headers.length).setValues(rows);

  // Add formulas for calculated columns (J=Weeks of Stock, K=Next Check Due, L=Overdue)
  for (let i = 0; i < rows.length; i++) {
    const row = i + 2;
    // Weeks of Stock = Current Stock / Avg Weekly Usage (if both > 0)
    sheet.getRange(row, 10).setFormula(`=IF(AND(C${row}<>"",I${row}>0), ROUND(C${row}/I${row},1), "")`);

    // Next Check Due = Last Checked + frequency in days
    sheet.getRange(row, 11).setFormula(
      `=IF(E${row}="","",IF(F${row}="Weekly",E${row}+7,IF(F${row}="Fortnightly",E${row}+14,IF(F${row}="Monthly",E${row}+30,""))))`
    );
    sheet.getRange(row, 11).setNumberFormat("dd/MM/yyyy");

    // Overdue = today > next check due
    sheet.getRange(row, 12).setFormula(`=IF(K${row}="","",IF(TODAY()>K${row},"YES",""))`);
  }

  // Conditional format: highlight overdue rows
  const range = sheet.getRange(2, 1, rows.length, headers.length);
  const rule = SpreadsheetApp.newConditionalFormatRule()
    .whenFormulaSatisfied(`=$L2="YES"`)
    .setBackground("#fce8e6")
    .setRanges([range])
    .build();
  sheet.setConditionalFormatRules([rule]);

  sheet.setFrozenRows(1);
  sheet.showSheet();
  SpreadsheetApp.getUi().alert(`DISPOSALS tab created with ${rows.length} items. Fill in Current Stock column to start tracking.`);
}

function generateDisposalReorderList() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName("DISPOSALS");
  if (!sheet) {
    SpreadsheetApp.getUi().alert("DISPOSALS tab not found. Run 'Seed Disposals Tab' first.");
    return;
  }

  const data = sheet.getDataRange().getValues();
  const lowStock = [];

  for (let i = 1; i < data.length; i++) {
    const name = data[i][0];
    const currentStock = Number(data[i][2]);
    const reorderTrigger = Number(data[i][6]);
    const reorderQty = data[i][7];
    const unit = data[i][3];
    const primarySupplier = data[i][12];
    const primaryURL = data[i][13];
    const primaryPrice = data[i][14];

    if (!name) continue;
    if (currentStock === 0 || isNaN(currentStock)) continue; // blank = not checked
    if (currentStock <= reorderTrigger) {
      lowStock.push({ name, currentStock, reorderTrigger, reorderQty, unit, primarySupplier, primaryURL, primaryPrice });
    }
  }

  if (lowStock.length === 0) {
    SpreadsheetApp.getUi().alert("Nothing to reorder — all items are above their trigger level.");
    return;
  }

  let msg = `REORDER LIST — ${lowStock.length} items need restocking:\n\n`;
  lowStock.forEach(item => {
    msg += `• ${item.name} — have ${item.currentStock} ${item.unit}, trigger is ${item.reorderTrigger}, order ${item.reorderQty}\n`;
    if (item.primarySupplier) msg += `  Supplier: ${item.primarySupplier}${item.primaryURL ? ` (${item.primaryURL})` : ""}\n`;
    msg += "\n";
  });

  SpreadsheetApp.getUi().alert(msg);
}

function checkDisposalsDue() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName("DISPOSALS");
  if (!sheet) return;

  const data = sheet.getDataRange().getValues();
  const overdue = [];

  for (let i = 1; i < data.length; i++) {
    const name = data[i][0];
    const overdueFlagCell = data[i][11]; // Column L
    if (name && String(overdueFlagCell).toUpperCase() === "YES") {
      overdue.push(name);
    }
  }

  if (overdue.length === 0) {
    ss.toast("All disposal checks are up to date.", "Disposals", 4);
  } else {
    SpreadsheetApp.getUi().alert(`${overdue.length} disposal items are overdue for a stock check:\n\n${overdue.join("\n")}\n\nOpen the DISPOSALS tab and update current stock levels.`);
  }
}

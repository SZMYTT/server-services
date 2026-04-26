// vendorOS — Phase 1: Sample Request Automation
// Copy into NNL_INVENTORY Google Sheets Script Editor

const NNL_WAREHOUSE_ADDRESS = `NNL
[Your Warehouse Address Line 1]
[Town, Postcode]`;

function generateSampleRequestsTab() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const vendorsSheet = ss.getSheetByName("VENDORS");
  const itemsSheet = ss.getSheetByName("ITEMS");
  const auditSheet = ss.getSheetByName("VENDOR_AUDIT");

  if (!vendorsSheet || !itemsSheet) {
    SpreadsheetApp.getUi().alert("Error: VENDORS or ITEMS sheet missing.");
    return;
  }

  ss.toast("Building sample requests list...", "vendorOS", 3);

  const vendorsData = vendorsSheet.getDataRange().getValues();
  const itemsData = itemsSheet.getDataRange().getValues();

  // Build vendor map: vendor no → {name, email, category, status}
  const vendorMap = {};
  for (let i = 1; i < vendorsData.length; i++) {
    const vNo = String(vendorsData[i][0]).trim();
    if (!vNo) continue;
    vendorMap[vNo] = {
      name: vendorsData[i][1],
      email: vendorsData[i][4],
      contactName: "" // MRP Easy doesn't store contact name separately — fill manually
    };
  }

  // Get active vendor statuses from VENDOR_AUDIT if it exists
  const activeVendors = new Set();
  if (auditSheet) {
    const auditData = auditSheet.getDataRange().getValues();
    for (let i = 1; i < auditData.length; i++) {
      const vNo = String(auditData[i][0]).trim();
      const status = String(auditData[i][11]).trim();
      if (status === "Active") activeVendors.add(vNo);
    }
  } else {
    // No audit sheet: include all vendors
    Object.keys(vendorMap).forEach(v => activeVendors.add(v));
  }

  // Build: items per vendor (only active vendors)
  const vendorItems = {};
  for (let i = 1; i < itemsData.length; i++) {
    const partNo = String(itemsData[i][0]).trim();
    const desc = String(itemsData[i][1]).trim();
    const vendorNo = String(itemsData[i][24]).trim();   // Y = Vendor No
    const vendorPartNo = String(itemsData[i][26]).trim(); // AA = Vendor Part No
    const isProcured = itemsData[i][20];                // U = Is procured item

    if (!partNo || !vendorNo || !activeVendors.has(vendorNo)) continue;
    if (!isProcured && isProcured !== "" && isProcured !== true && isProcured !== "TRUE") continue;

    if (!vendorItems[vendorNo]) vendorItems[vendorNo] = [];
    vendorItems[vendorNo].push({ partNo, desc, vendorPartNo });
  }

  // Count how many vendors each part number has (to flag single-source)
  const vendorCountPerPart = {};
  for (let i = 1; i < itemsData.length; i++) {
    const partNo = String(itemsData[i][0]).trim();
    const vendorNo = String(itemsData[i][24]).trim();
    if (!partNo || !vendorNo) continue;
    vendorCountPerPart[partNo] = (vendorCountPerPart[partNo] || 0) + 1;
  }

  // Build output rows
  const headers = [
    "Vendor No", "Vendor Name", "Vendor Email",
    "Part No", "Part Description", "Vendor SKU",
    "Priority", "Single Source?",
    "Email Drafted", "Email Sent Date",
    "Response Received", "Sample Received", "Sample Received Date",
    "Evaluation Status", "Evaluation Notes", "Outcome"
  ];

  const output = [];
  for (const vendorNo of activeVendors) {
    const items = vendorItems[vendorNo] || [];
    if (items.length === 0) continue;

    const v = vendorMap[vendorNo];
    for (const item of items) {
      const isSingleSource = (vendorCountPerPart[item.partNo] || 1) === 1;
      const priority = isSingleSource ? "HIGH" : "MEDIUM";
      output.push([
        vendorNo, v ? v.name : "", v ? v.email : "",
        item.partNo, item.desc, item.vendorPartNo,
        priority, isSingleSource ? "Yes" : "No",
        "N", "", // Email Drafted, Email Sent Date
        "N", "N", "", // Response, Sample, Sample Date
        "Pending", "", "" // Evaluation Status, Notes, Outcome
      ]);
    }
  }

  // Sort: HIGH priority first, then by vendor
  output.sort((a, b) => {
    if (a[6] !== b[6]) return a[6] === "HIGH" ? -1 : 1;
    return String(a[1]).localeCompare(String(b[1]));
  });

  let sampleSheet = ss.getSheetByName("SAMPLE_REQUESTS");
  if (!sampleSheet) sampleSheet = ss.insertSheet("SAMPLE_REQUESTS");
  sampleSheet.clear();
  sampleSheet.getRange(1, 1, 1, headers.length).setValues([headers]).setFontWeight("bold");
  if (output.length > 0) {
    sampleSheet.getRange(2, 1, output.length, headers.length).setValues(output);
  }

  // Highlight HIGH priority rows
  for (let i = 0; i < output.length; i++) {
    if (output[i][6] === "HIGH") {
      sampleSheet.getRange(i + 2, 1, 1, headers.length).setBackground("#fff3cd");
    }
  }

  sampleSheet.setFrozenRows(1);
  sampleSheet.showSheet();
  ss.toast(`${output.length} items across ${Object.keys(vendorItems).length} vendors. Ready to draft emails.`, "Sample Requests Built", 6);
}

function generateSampleRequestEmails() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sampleSheet = ss.getSheetByName("SAMPLE_REQUESTS");

  if (!sampleSheet) {
    SpreadsheetApp.getUi().alert("Run 'Generate Sample Requests Tab' first.");
    return;
  }

  const data = sampleSheet.getDataRange().getValues();
  if (data.length < 2) {
    SpreadsheetApp.getUi().alert("No data in SAMPLE_REQUESTS tab.");
    return;
  }

  ss.toast("Drafting sample request emails...", "vendorOS", 3);

  // Group rows by vendor (only where Email Drafted = N)
  const byVendor = {};
  for (let i = 1; i < data.length; i++) {
    const vendorNo = String(data[i][0]).trim();
    const vendorName = String(data[i][1]).trim();
    const email = String(data[i][2]).trim();
    const partNo = String(data[i][3]).trim();
    const desc = String(data[i][4]).trim();
    const vendorSKU = String(data[i][5]).trim();
    const isSingleSource = String(data[i][7]).trim() === "Yes";
    const drafted = String(data[i][8]).trim();

    if (!vendorNo || !email || drafted === "Y") continue;

    if (!byVendor[vendorNo]) {
      byVendor[vendorNo] = { name: vendorName, email, items: [], hasSingleSource: false };
    }
    byVendor[vendorNo].items.push({ partNo, desc, vendorSKU });
    if (isSingleSource) byVendor[vendorNo].hasSingleSource = true;
  }

  let drafted = 0;
  let skipped = 0;

  for (const vendorNo in byVendor) {
    const v = byVendor[vendorNo];
    if (!v.email || v.email === "") { skipped++; continue; }

    const itemLines = v.items.map(item => {
      const sku = item.vendorSKU ? ` (your ref: ${item.vendorSKU})` : "";
      return `  - ${item.desc}${sku}`;
    }).join("\n");

    const singleSourceNote = v.hasSingleSource
      ? `\nWe're also exploring backup sourcing options for some of these product categories. ` +
        `If you're able to suggest any comparable alternatives or additional product lines, ` +
        `we'd be grateful to know.\n`
      : "";

    const body = `Hi there,

I hope you're well. We're currently reviewing our supplier portfolio at NNL and would like to request samples of the following products we source from you:

${itemLines}

Could you please send samples to our warehouse address below? We'd also appreciate any current product specification sheets if available.
${singleSourceNote}
${NNL_WAREHOUSE_ADDRESS}

Many thanks,
Daniel
NNL`;

    try {
      GmailApp.createDraft(v.email, `Sample Request — ${v.name}`, body);
      drafted++;
    } catch (e) {
      console.error(`Failed to draft email for ${v.name}: ${e.message}`);
      skipped++;
    }
  }

  // Mark rows as drafted
  const today = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "dd/MM/yyyy");
  for (let i = 1; i < data.length; i++) {
    const vendorNo = String(data[i][0]).trim();
    if (byVendor[vendorNo] && data[i][8] !== "Y") {
      sampleSheet.getRange(i + 1, 9).setValue("Y");   // Email Drafted
    }
  }

  ss.toast(`${drafted} Gmail drafts created. ${skipped} skipped (no email). Review in Gmail before sending.`, "Emails Drafted", 8);
}

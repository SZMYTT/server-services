# PrismaOS — Future Ideas & Backlog

> A living document. Add ideas here freely — nothing here is scoped yet.
> Review this whenever starting a new phase.
> Last updated: 2026-04-20

---

## 🛒 Order & Stock Management (ERP-Lite)

### Concept
An embedded lightweight ERP inside PrismaOS, NOT a separate platform.
Uses the existing PostgreSQL database + Discord commands + Web UI panel.
No bloated SaaS, no monthly fee, no new logins.

### Core Idea: "Just Enough ERP"
Three tables added to the existing database:
- `orders` — order records (customer name, address, items, status, courier label URL)
- `stock_items` — product catalogue with current count, low-stock threshold
- `stock_movements` — audit log of all stock changes (sold, added, wasted)

### Discord Layer
New `/order` commands for the workspace:
- `/order new [customer] [item] [qty]` — log a new order
- `/order shipped [order_id]` — mark dispatched, trigger label generation
- `/stock check` — see low-stock items instantly
- `/stock add [item] [qty]` — restock something

### Label Generation
- Integrate Evri / Royal Mail Click & Drop API
- On `/order shipped`, auto-generate a label PDF link
- Post link directly in Discord so Alice can print from her phone or computer

### Web UI Panel
- New "Orders" tab in the Web Dashboard showing all orders by status
- Simple Kanban view: `Pending → Packed → Shipped → Delivered`
- Stock levels table with red highlighting for anything under threshold

### Who Would Use This?
- **Alice (Candles):** Main use case. Inbound Etsy orders, shipping labels, stock of wax/jars
- **Eddie (Cars):** Much simpler — just a vehicle inventory/status log
- **Alicja (Food Brand):** If she starts selling physical products

---

## 🔖 Shipment Label Generation
- Royal Mail Click & Drop API (free for Royal Mail Business account holders)
- Evri API (free)
- DHL Express API (enterprise tier later)
- Generate label → post PDF link to Discord → Mark order shipped automatically

---

## 📸 Image Pipeline (AI Vision)
- Alice photos candle → uploads to Discord channel → PrismaOS auto-crops/enhances, generates 3 caption variants, posts to Etsy/Instagram
- Use `llava:13b` vision model via Ollama (already in plan for Phase 2.5 model catalogue)
- Could use `moondream:1.8b` as a fast/cheap classifier for image categorisation

---

## 📅 Booking System (Asta — Nursing & Massage)
- Integrate Cal.com (open source, self-hostable) on the Lenovo server
- PrismaOS reads Cal.com calendar via API
- Discord command `/book` shows available slots
- Sends confirmation email (via Gmail API) automatically
- Future: send reminder SMS (Twilio free tier)

---

## 📱 TikTok Video Script Generator
- Alicja gives a topic → PrismaOS generates a TikTok script
- Outputs: hook (first 3 seconds), main body, call to action, hashtags
- Future: auto-upload to TikTok via API

---

## 🏠 Property Deal Analyser
- Input: Rightmove/Zoopla listing URL → PrismaOS scrapes details
- Calculates: gross yield, stamp duty, estimated refurb cost, breakeven timeline
- Outputs a formatted "deal sheet" PDF to Discord and Web UI

---

## 🚗 DVLA / MOT History Checker
- `/mot [reg]` Discord command → fetches MOT history via official Gov API (free)
- PrismaOS summarises pass/fail history, flags advisories pattern
- Combine with HPI check data for pre-auction intelligence

---

## 💌 Email Newsletter Automation
- Draft monthly newsletters for each workspace
- Uses Gmail API to send
- Alice → Etsy customer list
- Alicja → food brand subscribers
- Asta → local patient email list

---

## 🧠 RAG Memory (Vector Search)
- Install ChromaDB (already running at port 8001!)
- Any completed research task → auto-embed into vector store
- Future research tasks can search past outputs before hitting the web
- Prevents duplicate research, builds institutional memory

---

## 📊 Advanced Analytics Dashboard
- Monthly income estimates per workspace (from task outputs)
- Engagement metrics from Facebook/Instagram API
- Etsy views/sales trend chart
- Compare month vs month

---

## 🌐 Website Builder Module Automation
- Generate full HTML/CSS website drafts from a brief
- Host on Caddy (already running!)
- For Alice: simple Etsy-style product page
- For Asta: booking page with Cal.com embed

---

## 🔔 Push Notification System (ntfy)
- ntfy is already running at port 8002!
- Push alerts to phones when:
  - A task needs approval
  - A task is done
  - Stock is low
  - A new Etsy order arrives

---

## 🤖 Self-Learning Preferences
- Track which content Alice/Asta actually approved vs declined
- After 20+ samples, fine-tune prompts to match their actual preferences
- "Preference weights" stored per workspace in the DB

---

## 🗂️ Document Storage & Analysis
- Receipts, invoices, contracts uploaded to Lenovo via Discord
- PrismaOS extracts key info (amount, date, vendor, category)
- Auto-logs to finance module for each workspace

---

*Add ideas freely — review quarterly.*

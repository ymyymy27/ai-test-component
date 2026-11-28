const express = require("express");
const ordersRouter = require("./routes/orders");

const app = express();
app.use("/api/orders", ordersRouter);
app.get("/health", (_req, res) => res.json({ ok: true }));

module.exports = app;

const express = require("express");
const router = express.Router();

router.get("/", (_req, res) => res.json([]));
router.post("/", (_req, res) => res.status(201).json({ id: "o1" }));
router.get("/:id", (req, res) => res.json({ id: req.params.id }));
router.delete("/:id", (_req, res) => res.status(204).end());

module.exports = router;

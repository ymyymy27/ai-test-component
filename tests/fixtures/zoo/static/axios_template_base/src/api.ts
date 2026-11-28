import axios from "axios";

const API_HOST = "http://localhost:8000";
const api = axios.create({
  baseURL: `${API_HOST}/api/v1`,
});

export function fetchOrders() {
  return api.get("/orders");
}

export function createOrder(title: string) {
  return api.post("/orders", { title, amount: 10 });
}

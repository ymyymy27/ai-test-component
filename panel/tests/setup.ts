import { afterEach } from "vitest";

afterEach(() => {
  document.body.innerHTML = "";
  delete (window as { aitestHost?: unknown }).aitestHost;
});

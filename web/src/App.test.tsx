import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { expect, test, vi } from "vitest";
import App from "./App";

vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => [] }));

test("renders the Phase 1 template library", async () => {
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><App /></MemoryRouter></QueryClientProvider>);
  expect(screen.getByText(/Make every field/)).toBeInTheDocument();
  expect(await screen.findByText("Drop in a PDF or workbook")).toBeInTheDocument();
});

import type { DeviceDetail } from "./types";

export interface InventoryMetadata {
  source: string | undefined;
  status: string | undefined;
  model: string | undefined;
  firmware: string | undefined;
  lan_ip: string | undefined;
  mac: string | undefined;
  network_name: string | undefined;
  external_url: string | undefined;
  region: string | undefined;
  product_type: string | undefined;
  checked_at: string | undefined;
}

function text(data: Record<string, unknown>, key: string): string | undefined {
  const value = data[key];
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

export function inventoryOf(device: Pick<DeviceDetail, "live" | "provider"> | undefined): InventoryMetadata {
  const adapter = device?.provider === "meraki" ? device.live.meraki : undefined;
  const data = device?.live.inventory ?? adapter ?? {};
  return {
    source: text(data, "source") ?? device?.provider ?? undefined,
    status: text(data, "status"),
    model: text(data, "model"),
    firmware: text(data, "firmware"),
    lan_ip: text(data, "lan_ip"),
    mac: text(data, "mac"),
    network_name: text(data, "network_name"),
    external_url: text(data, "external_url") ?? (adapter ? text(adapter, "dashboard_url") : undefined),
    region: text(data, "region"),
    product_type: text(data, "product_type"),
    checked_at: text(data, "checked_at"),
  };
}

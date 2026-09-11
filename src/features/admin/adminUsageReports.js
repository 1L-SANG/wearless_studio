export function replaceUsageReportStatus(items, updated) {
  return items.map((item) => (
    item.id === updated.id ? { ...item, status: updated.status } : item
  ));
}

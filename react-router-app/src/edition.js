export function editionLabelFromCategories(categories = []) {
  if (categories.includes("Print Edition")) {
    return "Print Edition";
  }
  if (categories.includes("Online Only")) {
    return "Online Only";
  }
  return "";
}


export function parseReceiptHash(hash) {
  // Some chat clients preserve a Markdown escape before the separator.
  const params = new URLSearchParams(
    hash.replace(/^#/, "").replace(/\\(?=&)/g, ""),
  );
  if (!params.has("receipt")) return null;
  const id = params.get("receipt"),
    receiptToken = params.get("token");
  if (
    !id ||
    !receiptToken ||
    !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(
      id || "",
    ) ||
    !/^[a-f0-9]{64}$/.test(receiptToken || "")
  )
    throw Error(
      "This receipt link is incomplete or invalid. Open the full saved link, including the text after #.",
    );
  return { id, receiptToken };
}

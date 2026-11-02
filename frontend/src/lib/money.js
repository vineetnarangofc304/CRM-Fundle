export const inr = (n) => `₹${Math.round(Number(n) || 0).toLocaleString("en-IN")}`;

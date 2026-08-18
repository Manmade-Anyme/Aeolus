/**
 * Validates if the provided password meets strength requirements.
 * @param password - The string to validate
 * @returns true if valid, false otherwise
 */
export function isStrongPassword(password: string): boolean {
  if (password.length < 8) {
    return false;
  }
  if (!/[A-Z]/.test(password)) {
    return false;
  }
  if (!/[0-9]/.test(password)) {
    return false;
  }
  return true;
}

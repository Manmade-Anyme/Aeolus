import { isStrongPassword } from '../../src/utils/password';

describe('isStrongPassword', () => {
  it('returns false for passwords shorter than 8 characters', () => {
    expect(isStrongPassword('A1b')).toBe(false);
  });

  it('returns false for passwords without uppercase letters', () => {
    expect(isStrongPassword('a1bcdefgh')).toBe(false);
  });

  it('returns false for passwords without numbers', () => {
    expect(isStrongPassword('Abcdefgh')).toBe(false);
  });

  it('returns true for strong passwords', () => {
    expect(isStrongPassword('Abcdefg1')).toBe(true);
    expect(isStrongPassword('1234567A')).toBe(true);
  });
});

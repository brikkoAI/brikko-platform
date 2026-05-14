import { describe, expect, it } from 'vitest';
import { signupSchema } from '@/components/auth/SignupForm';

describe('signupSchema', () => {
  it('пропускает валидные данные', () => {
    const result = signupSchema.safeParse({
      email: 'test@example.com',
      password: 'password12',
      acceptTerms: true,
    });
    expect(result.success).toBe(true);
  });

  it('требует валидный email', () => {
    const result = signupSchema.safeParse({
      email: 'not-an-email',
      password: 'password12',
      acceptTerms: true,
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues[0]?.path).toEqual(['email']);
    }
  });

  it('требует пароль ≥10 символов', () => {
    const result = signupSchema.safeParse({
      email: 'test@example.com',
      password: 'short',
      acceptTerms: true,
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues[0]?.path).toEqual(['password']);
      expect(result.error.issues[0]?.message).toMatch(/Минимум 10/);
    }
  });

  it('требует acceptTerms=true', () => {
    const result = signupSchema.safeParse({
      email: 'test@example.com',
      password: 'password12',
      acceptTerms: false,
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues[0]?.path).toEqual(['acceptTerms']);
    }
  });

  it('обрезает пробелы — но не само поле; trimming на forms-уровне', () => {
    // Схема принимает email с пробелами; trim делается перед submit (см. SignupForm.onSubmit)
    const result = signupSchema.safeParse({
      email: '  test@example.com  ',
      password: 'password12',
      acceptTerms: true,
    });
    // email с пробелами не валиден по zod-email — это нормально, форма trim'ит до сабмита
    expect(result.success).toBe(false);
  });
});

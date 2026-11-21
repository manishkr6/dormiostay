import { z } from 'zod';

const showcaseSchema = z.object({
  fullName: z.string().min(2, 'Name must be at least 2 characters'),
  studentId: z.string().min(5, 'Student ID must be at least 5 characters'),
  notes: z.string().max(100, 'Notes cannot exceed 100 characters').optional(),
  agreeTerms: z.boolean().refine((val) => val === true, {
    message: 'You must accept the terms to proceed',
  }),
});

describe('Showcase Form Zod Validation Foundation', () => {
  it('validates a correct form payload', () => {
    const validData = {
      fullName: 'Tenzing Norbu',
      studentId: 'ICFAI-2026-001',
      notes: 'Need ground floor room if possible',
      agreeTerms: true,
    };

    const result = showcaseSchema.safeParse(validData);
    expect(result.success).toBe(true);
  });

  it('rejects short full names', () => {
    const data = {
      fullName: 'A',
      studentId: 'ICFAI-2026-001',
      agreeTerms: true,
    };

    const result = showcaseSchema.safeParse(data);
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues[0]?.message).toBe('Name must be at least 2 characters');
    }
  });

  it('rejects short student IDs', () => {
    const data = {
      fullName: 'Tenzing Norbu',
      studentId: '123',
      agreeTerms: true,
    };

    const result = showcaseSchema.safeParse(data);
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues[0]?.message).toBe('Student ID must be at least 5 characters');
    }
  });

  it('rejects unaccepted terms', () => {
    const data = {
      fullName: 'Tenzing Norbu',
      studentId: 'ICFAI-2026-001',
      agreeTerms: false,
    };

    const result = showcaseSchema.safeParse(data);
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues[0]?.message).toBe('You must accept the terms to proceed');
    }
  });

  it('rejects overly long notes', () => {
    const data = {
      fullName: 'Tenzing Norbu',
      studentId: 'ICFAI-2026-001',
      notes: 'x'.repeat(101),
      agreeTerms: true,
    };

    const result = showcaseSchema.safeParse(data);
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues[0]?.message).toBe('Notes cannot exceed 100 characters');
    }
  });
});

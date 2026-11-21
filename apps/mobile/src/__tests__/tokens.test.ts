import { tokens } from '../theme';

describe('DormioStay Mobile Brand Tokens', () => {
  it('defines all required brand colors from Frontend Specification', () => {
    expect(tokens.colors.indigo).toBe('#312e81');
    expect(tokens.colors.violet).toBe('#6d4aff');
    expect(tokens.colors.lavender).toBe('#ede9fe');
    expect(tokens.colors.ivory).toBe('#faf9f6');
    expect(tokens.colors.charcoal).toBe('#171923');
    expect(tokens.colors.slate).toBe('#64748b');
    expect(tokens.colors.coral).toBe('#f97360');
    expect(tokens.colors.amber).toBe('#f59e0b');
  });

  it('defines accessible semantic feedback colors and text variants', () => {
    expect(tokens.colors.success).toBe('#16865b');
    expect(tokens.colors.successText).toBe('#116644');
    expect(tokens.colors.warningText).toBe('#92400e');
    expect(tokens.colors.error).toBe('#dc2626');
    expect(tokens.colors.errorText).toBe('#b91c1c');
    expect(tokens.colors.info).toBe('#2563eb');
    expect(tokens.colors.infoText).toBe('#1e40af');
  });

  it('defines minimum touch target meeting 44pt iOS / 48dp Android standards', () => {
    expect(tokens.target).toBeGreaterThanOrEqual(48);
  });

  it('configures Playfair Display for headings and Inter for body', () => {
    expect(tokens.fonts.heading).toBe('PlayfairDisplay_600SemiBold');
    expect(tokens.fonts.body).toBe('Inter_400Regular');
    expect(tokens.fonts.medium).toBe('Inter_500Medium');
    expect(tokens.fonts.bold).toBe('Inter_600SemiBold');
  });
});

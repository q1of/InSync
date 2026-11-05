// Положение «бегунка» переключателя .segment из count кнопок (padding и gap у .segment — по 4px).
export function thumbStyle(index: number, count: number) {
  const inner = `(100% - ${8 + (count - 1) * 4}px) / ${count}`;
  return { left: `calc(4px + ${index} * (${inner} + 4px))`, width: `calc(${inner})` };
}

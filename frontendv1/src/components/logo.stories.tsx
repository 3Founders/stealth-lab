import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Logo, Mark } from "./logo";

const meta = {
  title: "Brand/Logo",
  component: Logo,
  parameters: { layout: "centered" },
} satisfies Meta<typeof Logo>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Wordmark: Story = {
  render: () => <Logo className="h-10" />,
};

export const MarkSizes: Story = {
  render: () => (
    <div className="flex items-end gap-6">
      <Mark className="h-4 w-4" />
      <Mark className="h-8 w-8" />
      <Mark className="h-16 w-16" />
      <Mark className="h-32 w-32" />
    </div>
  ),
};

export const MarkOnBackgrounds: Story = {
  render: () => (
    <div className="flex gap-4">
      <div className="flex h-28 w-28 items-center justify-center rounded-2xl bg-paper p-4">
        <Mark className="h-full w-full" />
      </div>
      <div className="flex h-28 w-28 items-center justify-center rounded-2xl bg-ink p-4">
        <Mark className="h-full w-full" />
      </div>
      <div className="flex h-28 w-28 items-center justify-center rounded-2xl bg-yellow p-4">
        <Mark className="h-full w-full" />
      </div>
      <div className="flex h-28 w-28 items-center justify-center rounded-2xl bg-botanical p-4">
        <Mark className="h-full w-full" />
      </div>
    </div>
  ),
};

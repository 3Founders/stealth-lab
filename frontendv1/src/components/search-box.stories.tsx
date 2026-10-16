import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent } from "storybook/test";

import { SearchBox } from "./search-box";

const meta = {
  title: "Product/SearchBox",
  component: SearchBox,
  parameters: { layout: "padded", nextjs: { appDirectory: true } },
} satisfies Meta<typeof SearchBox>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Large: Story = {
  args: { size: "lg" },
};

export const Small: Story = {
  args: { size: "sm" },
};

export const WithInitialQuery: Story = {
  args: { size: "sm", initialQuery: "how do I debug a flaky test" },
};

export const Typing: Story = {
  args: { size: "lg" },
  play: async ({ canvas }) => {
    const input = canvas.getByRole("searchbox");
    await userEvent.type(input, "reduce coding-agent context");
    await expect(input).toHaveValue("reduce coding-agent context");
  },
};

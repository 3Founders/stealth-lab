import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn } from "storybook/test";

import { Button } from "./button";

const meta = {
  title: "UI/Button",
  component: Button,
  parameters: { layout: "centered" },
  args: { onClick: fn(), children: "Search keळ" },
} satisfies Meta<typeof Button>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  args: { variant: "default" },
};

export const Brand: Story = {
  args: { variant: "brand" },
};

export const Outline: Story = {
  args: { variant: "outline" },
};

export const Ghost: Story = {
  args: { variant: "ghost" },
};

export const Disabled: Story = {
  args: { disabled: true },
};

export const Click: Story = {
  args: { children: "Click me" },
  play: async ({ args, canvas }) => {
    const button = canvas.getByRole("button", { name: "Click me" });
    await button.click();
    await expect(args.onClick).toHaveBeenCalledOnce();
  },
};

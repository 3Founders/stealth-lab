import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Badge } from "./badge";

const meta = {
  title: "UI/Badge",
  component: Badge,
  parameters: { layout: "centered" },
  args: { children: "Verified" },
} satisfies Meta<typeof Badge>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = { args: { variant: "default" } };
export const Secondary: Story = { args: { variant: "secondary", children: "Experimental" } };
export const Accent: Story = { args: { variant: "accent", children: "Strong match" } };
export const Outline: Story = { args: { variant: "outline", children: "Task" } };
export const Brand: Story = { args: { variant: "brand", children: "New" } };

export const AllVariants: Story = {
  render: () => (
    <div className="flex flex-wrap gap-2">
      <Badge variant="default">Verified</Badge>
      <Badge variant="secondary">Experimental</Badge>
      <Badge variant="accent">Strong match</Badge>
      <Badge variant="outline">Task</Badge>
      <Badge variant="brand">New</Badge>
    </div>
  ),
};

import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Section } from "./section";

const meta = {
  title: "Brand/Section",
  component: Section,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof Section>;

export default meta;
type Story = StoryObj<typeof meta>;

const Body = ({ label }: { label: string }) => (
  <div className="py-16">
    <p className="text-xs uppercase tracking-widest opacity-50">{label}</p>
    <h2 className="mt-2 font-display text-3xl">Section content sits here</h2>
  </div>
);

export const Paper: Story = { args: { tone: "paper", children: <Body label="paper" /> } };
export const PaperDim: Story = { args: { tone: "paper-dim", children: <Body label="paper-dim" /> } };
export const Yellow: Story = { args: { tone: "yellow", children: <Body label="yellow" /> } };
export const Ink: Story = { args: { tone: "ink", children: <Body label="ink" /> } };
export const Botanical: Story = { args: { tone: "botanical", children: <Body label="botanical" /> } };
export const Cobalt: Story = { args: { tone: "cobalt", children: <Body label="cobalt" /> } };

export const Rhythm: Story = {
  args: { children: <Body label="paper" /> },
  render: () => (
    <>
      <Section tone="paper"><Body label="paper" /></Section>
      <Section tone="yellow"><Body label="yellow" /></Section>
      <Section tone="paper"><Body label="paper" /></Section>
      <Section tone="ink"><Body label="ink" /></Section>
      <Section tone="botanical"><Body label="botanical" /></Section>
    </>
  ),
};

import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/cn";

export type ButtonVariant = "primary" | "dark" | "ghost" | "link";
export type ButtonSize = "md" | "sm";

const BASE = "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[10px] border font-semibold transition-colors disabled:cursor-not-allowed";

const VARIANTS: Record<ButtonVariant, string> = {
  primary: "border-brand bg-brand text-white hover:bg-brand-hover disabled:border-disabled disabled:bg-disabled disabled:text-disabled-ink",
  dark: "border-ink bg-ink text-white hover:bg-[#1b2b43] disabled:border-disabled disabled:bg-disabled disabled:text-disabled-ink",
  ghost: "border-line-strong bg-white text-ink hover:bg-canvas disabled:border-disabled disabled:bg-disabled disabled:text-disabled-ink",
  link: "min-h-7 border-transparent bg-transparent px-0 font-semibold text-brand-link underline underline-offset-2 hover:text-brand-ink disabled:text-disabled-ink",
};

const SIZES: Record<ButtonSize, string> = {
  md: "min-h-11 px-4 text-sm",
  sm: "min-h-9 px-3 text-[13px]",
};

export function buttonClasses(variant: ButtonVariant = "primary", size: ButtonSize = "md", className?: string): string {
  return cn(BASE, VARIANTS[variant], variant === "link" ? "text-[13.5px]" : SIZES[size], className);
}

interface ButtonProps extends ComponentProps<"button"> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export function Button({ variant = "primary", size = "md", className, type = "button", ...props }: ButtonProps) {
  return <button type={type} className={buttonClasses(variant, size, className)} {...props} />;
}

interface ButtonLinkProps extends Omit<ComponentProps<typeof Link>, "className"> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  className?: string;
  children: ReactNode;
}

export function ButtonLink({ variant = "ghost", size = "md", className, ...props }: ButtonLinkProps) {
  return <Link className={buttonClasses(variant, size, className)} {...props} />;
}

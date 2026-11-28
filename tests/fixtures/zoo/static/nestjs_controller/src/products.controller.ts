import { Controller, Get, Post, Delete } from "@nestjs/common";

@Controller("api/products")
export class ProductsController {
  @Get()
  findAll() {
    return [];
  }

  @Post()
  create() {
    return { id: "p1" };
  }

  @Delete(":id")
  remove() {
    return { deleted: true };
  }
}
